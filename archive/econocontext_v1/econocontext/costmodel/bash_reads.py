"""Which file lines a shell command showed the agent, when the command only reads.

Why it exists: coding agents read files through the shell as much as through a read
tool (Claude Code's Explore workers ran `sed -n '600,700p' $F` 23 times in wres4). If
such a command counts as "a write to anything", every shell read makes all earlier
evidence stale and a worker's holdings look empty. So a command is parsed:

  read_only   every simple command in it is a known reader (cat, sed -n, head, tail,
              nl, grep, rg, ls, find, wc, echo, cd, git show/diff/log ...) and nothing is
              redirected into a file. Anything else (python, pytest, sed -i, mkdir,
              `> file`) is not read-only: the caller treats it as a change, as before.
  reads       the line ranges the output showed, per file:
                sed -n 'A,Bp' / 'Ap' / 'A,$p'    lines A..B
                cat, nl, sed without -n          the whole file
                head -n N / tail -n N / tail -n +K
                awk 'NR>=A && NR<=B'              lines A..B
                ... | head -n N                   the first N of those lines
                grep -n / rg -n                   the lines printed with a number (hits and
                                                  -A/-B/-C context), read from the OUTPUT
              A reader followed by a filter (`cat f | grep x`) showed only some lines,
              with no numbers: it adds no line ranges.

Scripts are split on newlines, `;`, `&&`, `||` and `|`. `cd DIR` and `NAME=value`
(expanded as $NAME / ${NAME}) are followed. `docker exec [-w DIR] C bash -lc '...'`
and `bash -c '...'` are unwrapped; `roots` maps a container path (/testbed) to the
host workspace it is mounted from.
What it must never do: call a shell, read a file, or call a command read-only when
it is not sure (unknown programs are not read-only).
"""

import os
import re
import shlex
from dataclasses import dataclass

READERS = {"cat", "nl", "sed", "head", "tail", "awk", "grep", "egrep", "fgrep", "rg"}
HARMLESS = {"ls", "find", "wc", "echo", "printf", "pwd", "cd", "sort", "uniq", "cut", "tr",
            "file", "stat", "tree", "true", "basename", "dirname", "which", "column", "diff",
            "realpath", "readlink", "du", "date", "source", ".", "export", "test", "["}
GIT_READS = {"show", "diff", "log", "status", "grep", "blame", "ls-files", "rev-parse"}
SHELLS = {"bash", "sh", "zsh"}
SEPARATORS = {";", "&&", "||", "\n", "&"}
DEFAULT_HEAD = 10
# Options that take a value, per program, so the value is not taken for a path.
VALUED = {"grep": set("ABCefmdD"), "egrep": set("ABCefmdD"), "fgrep": set("ABCefmdD"),
          "rg": set("ABCefgtTmMjr"), "head": set("nc"), "tail": set("nc"), "sed": set("ef"),
          "awk": set("fvF"), "nl": set("bdfhilnsvw")}
VARIABLE = re.compile(r"\$\{(\w+)\}|\$(\w+)")
ASSIGN = re.compile(r"^(\w+)=(.*)$", re.S)
SED_PART = re.compile(r"^\s*(\d+|\$)(?:\s*,\s*(\d+|\$|\+\d+))?\s*p\s*$")
AWK_RANGE = re.compile(r"NR\s*>=?\s*(\d+)\s*&&\s*NR\s*<=?\s*(\d+)|NR\s*==\s*(\d+)\s*,\s*NR\s*==\s*(\d+)")
PATHED = re.compile(r"^([^\s:]+?)([:-])(\d+)\2")  # path:12: (a hit) or path-12- (context)
BARE = re.compile(r"^(\d+)[:-]")


@dataclass(frozen=True)
class LineRead:
    """Lines of one file the agent saw: `start`..`end`, 1-based and inclusive. end None:
    to the end of the file. start < 0: the last -start lines (tail -n N)."""
    path: str      # absolute, on the host
    start: int
    end: int | None


@dataclass
class _Grep:
    cwd: str
    paths: list[str]     # absolute targets (files or directories); [] = the cwd
    numbered: bool


def _tokens(script: str) -> list[str]:
    lx = shlex.shlex(script, posix=True, punctuation_chars=";&|<>()\n")
    lx.whitespace, lx.whitespace_split, lx.commenters = " \t\r", True, ""
    try:
        return list(lx)
    except ValueError:  # unbalanced quotes: not parsed, so not read-only
        return ["\x00"]


def _commands(tokens: list[str]) -> list[list[list[str]]]:
    """Tokens -> commands -> pipeline stages -> words. Comment lines are dropped."""
    commands, stages, words, comment = [], [], [], False
    for t in tokens + ["\n"]:
        if comment:
            comment = t != "\n"
            continue
        if t.startswith("#") and not words:
            comment = True
            continue
        if t in SEPARATORS or t == "|":
            stages.append(words)
            words = []
            if t != "|":
                commands.append([s for s in stages if s])
                stages = []
        else:
            words.append(t)
    return [c for c in commands if c]


def _redirects(words: list[str]) -> tuple[list[str], bool]:
    """Words without harmless redirections (2>/dev/null, 2>&1); False if the stage
    writes a file."""
    out, i, safe = [], 0, True
    while i < len(words):
        w = words[i]
        if w in (">", ">>", ">&", "&>"):
            target = words[i + 1] if i + 1 < len(words) else ""
            if out and out[-1] in ("1", "2", "&"):
                out.pop()
            if not (target == "/dev/null" or target in ("1", "2")):
                safe = False
            i += 2
            continue
        if w == "<":  # input redirection reads a file: still read-only
            i += 2
            continue
        out.append(w)
        i += 1
    return out, safe


class _Parser:
    def __init__(self, cwd: str, roots: dict[str, str]):
        self.cwd, self.roots = cwd, roots
        self.vars: dict[str, str] = {}
        self.reads: list[LineRead] = []
        self.greps: list[_Grep] = []
        self.read_only = True

    def path(self, word: str) -> str:
        for inside, host in self.roots.items():
            if word == inside or word.startswith(inside.rstrip("/") + "/"):
                word = host + word[len(inside.rstrip("/")):]
                break
        return os.path.normpath(os.path.join(self.cwd, word))

    def expand(self, word: str) -> str:
        return VARIABLE.sub(lambda m: self.vars.get(m.group(1) or m.group(2), m.group(0)), word)

    def script(self, text: str) -> None:
        for command in _commands(_tokens(text)):
            self.command(command)

    def command(self, stages: list[list[str]]) -> None:
        shown: list[LineRead] = []
        for i, raw in enumerate(stages):
            words, safe = _redirects(raw)
            self.read_only &= safe
            words = [self.expand(w) for w in words]
            while words and ASSIGN.match(words[0]):  # NAME=value [command]
                name, value = ASSIGN.match(words.pop(0)).groups()
                self.vars[name] = value
            if not words:
                continue
            if words[0] == "export":
                for w in words[1:]:
                    if ASSIGN.match(w):
                        self.vars.update([ASSIGN.match(w).groups()])
                continue
            if i == 0:
                shown = self.stage(words)
            else:
                shown = self.filter(words, shown)
        self.reads.extend(shown)

    def stage(self, words: list[str]) -> list[LineRead]:
        """The first stage of a pipeline: what it read and showed."""
        prog, args = os.path.basename(words[0]), words[1:]
        if prog == "timeout" and args:
            return self.stage(args[1:])
        if prog == "docker" and args[:1] == ["exec"]:
            return self.docker(args[1:])
        if prog in SHELLS:
            flags = [a for a in args if a.startswith("-")]
            if any("c" in f for f in flags) and len(args) > len(flags):
                self.script(args[len(flags)])
            else:
                self.read_only = False
            return []
        if prog == "cd":
            self.cwd = self.path(args[0]) if args else self.cwd
            return []
        if prog == "git":
            sub = next((a for a in args if not a.startswith("-")), "")
            self.read_only &= sub in GIT_READS
            return []
        if prog in HARMLESS:
            if prog == "find" and any(a in ("-exec", "-execdir", "-delete", "-ok") for a in args):
                self.read_only = False
            return []
        if prog not in READERS:
            self.read_only = False
            return []
        flags, operands = self.split(prog, args)
        if prog in ("grep", "egrep", "fgrep", "rg"):
            if "e" not in flags and "f" not in flags and operands:
                operands = operands[1:]  # the pattern
            self.greps.append(_Grep(self.cwd, [self.path(p) for p in operands],
                                    "n" in flags or "line-number" in flags))
            return []
        if prog == "sed":
            if "i" in flags or "in-place" in flags:
                self.read_only = False
                return []
            scripts = flags.get("e") or ([operands.pop(0)] if operands else [])
            if "n" not in flags:
                return [LineRead(self.path(p), 1, None) for p in operands]
            spans = [_sed_spans(script) for script in scripts]
            if not spans or None in spans:
                return []
            return [LineRead(self.path(p), a, b) for p in operands for part in spans for a, b in part]
        if prog == "awk":
            program = operands.pop(0) if operands and "f" not in flags else ""
            if "system(" in program or re.search(r"print[^;]*>", program):
                self.read_only = False
                return []
            m = AWK_RANGE.search(program)
            if not m:
                return []
            a, b = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(3)), int(m.group(4)))
            return [LineRead(self.path(p), a, b) for p in operands]
        if prog in ("head", "tail"):
            n = _count(flags, prog)
            if n is None:
                return []
            if prog == "head":
                return [LineRead(self.path(p), 1, n) for p in operands]
            start = int(n[1:]) if isinstance(n, str) else -n  # tail -n +K: from line K
            return [LineRead(self.path(p), start, None) for p in operands]
        return [LineRead(self.path(p), 1, None) for p in operands]  # cat, nl

    def filter(self, words: list[str], shown: list[LineRead]) -> list[LineRead]:
        """A later pipeline stage. `head -n N` keeps the first N shown lines (one file);
        any other reader or harmless program hides line numbers: nothing is shown."""
        prog = os.path.basename(words[0])
        if prog not in READERS and prog not in HARMLESS:
            self.read_only = False
            return []
        if prog in ("grep", "egrep", "fgrep", "rg"):  # grep as a filter: hits carry no numbers
            return []
        if prog == "head" and len(shown) == 1 and shown[0].start > 0:
            flags, operands = self.split(prog, words[1:])
            n = _count(flags, prog)
            if isinstance(n, int) and not operands:
                r = shown[0]
                end = r.start + n - 1
                return [LineRead(r.path, r.start, end if r.end is None else min(r.end, end))]
        return []

    def docker(self, args: list[str]) -> list[LineRead]:
        """docker exec [-w DIR] [-e X] [-it] CONTAINER COMMAND..."""
        i, cwd = 0, self.cwd
        while i < len(args) and args[i].startswith("-"):
            if args[i] in ("-w", "--workdir"):
                cwd = self.path(args[i + 1])
                i += 2
            elif args[i] in ("-e", "--env", "-u", "--user"):
                i += 2
            else:
                i += 1
        saved, self.cwd = self.cwd, cwd
        shown = self.stage(args[i + 1:]) if len(args) > i + 1 else []
        self.cwd = saved
        return shown

    @staticmethod
    def split(prog: str, args: list[str]) -> tuple[dict, list[str]]:
        """Options (name -> list of values; flags -> []) and operands."""
        valued, flags, operands, i = VALUED.get(prog, set()), {}, [], 0
        while i < len(args):
            a = args[i]
            if a == "--":
                operands.extend(args[i + 1:])
                break
            if a.startswith("--"):
                name, _, value = a[2:].partition("=")
                flags.setdefault(name, []).extend([value] if value else [])
            elif a.startswith("-") and len(a) > 1:
                body = a[1:]
                if body.isdigit():  # head -30
                    flags.setdefault("n", []).append(body)
                else:
                    for j, ch in enumerate(body):
                        if ch in valued:
                            value = body[j + 1:] or (args[i + 1] if i + 1 < len(args) else "")
                            if not body[j + 1:]:
                                i += 1
                            flags.setdefault(ch, []).append(value)
                            break
                        flags.setdefault(ch, [])
            else:
                operands.append(a)
            i += 1
        return flags, operands


def _sed_spans(script: str) -> list[tuple[int, int | None]] | None:
    """sed -n print ranges ('10,20p;30p'); None when the script does anything else."""
    spans = []
    for part in re.split(r"[;\n]", script):
        if not part.strip():
            continue
        m = SED_PART.match(part)
        if not m or m.group(1) == "$":
            return None
        a, b = int(m.group(1)), m.group(2)
        if b is None:
            spans.append((a, a))
        elif b == "$":
            spans.append((a, None))
        elif b.startswith("+"):
            spans.append((a, a + int(b[1:])))
        else:
            spans.append((a, int(b)))
    return spans


def _count(flags: dict, prog: str) -> int | str | None:
    """head/tail -n N (or -N); tail -n +K gives '+K'. None: bytes (-c) or unparsed."""
    if "c" in flags:
        return None
    value = (flags.get("n") or flags.get("lines") or [str(DEFAULT_HEAD)])[-1]
    if prog == "tail" and value.startswith("+") and value[1:].isdigit():
        return value
    return int(value) if value.isdigit() else None


def grep_lines(output: str, greps: list[_Grep]) -> list[LineRead]:
    """Numbered lines in the output of `grep -n` / `rg -n`: `path:12:text` and
    `path-12-text` (context), or `12:text` when every grep read the same one file."""
    numbered = [g for g in greps if g.numbered]
    if not numbered:
        return []
    targets = {tuple(g.paths) for g in greps}  # one file for every grep: bare numbers are its lines
    only = next(iter(targets)) if len(targets) == 1 else ()
    single = only[0] if len(only) == 1 and not os.path.isdir(only[0]) else None
    found = []
    for line in output.splitlines():
        m = PATHED.match(line)
        path = m and next((c for g in numbered
                           for c in [os.path.normpath(os.path.join(g.cwd, m.group(1)))]
                           if os.path.isfile(c) and any(c == t or c.startswith(t.rstrip("/") + "/")
                                                        for t in (g.paths or [g.cwd]))), None)
        if path:
            found.append(LineRead(path, int(m.group(3)), int(m.group(3))))
        elif single and (bare := BARE.match(line)):
            found.append(LineRead(single, int(bare.group(1)), int(bare.group(1))))
    return found


def shell_reads(command: str, output: str, cwd: str,
                roots: dict[str, str] | None = None) -> tuple[bool, list[LineRead]]:
    """(read_only, the file lines the command's output showed)."""
    p = _Parser(os.path.normpath(cwd), roots or {})
    p.script(command)
    return p.read_only, p.reads + grep_lines(output, p.greps)


def resolve(read: LineRead, n_lines: int) -> tuple[int, int] | None:
    """A LineRead as concrete lines a..b of a file of n_lines lines (None if empty)."""
    if read.start < 0:
        a, b = max(1, n_lines + read.start + 1), n_lines
    else:
        a, b = read.start, n_lines if read.end is None else min(read.end, n_lines)
    return (a, b) if 1 <= a <= b else None


def merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Overlapping or touching ranges joined, sorted."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def count(ranges: list[tuple[int, int]]) -> int:
    return sum(b - a + 1 for a, b in merge(ranges))


def line_overlap(held: dict[str, list[tuple[int, int]]],
                 needed: dict[str, list[tuple[int, int]]]) -> float | None:
    """The share of the needed lines already held (same file, overlapping lines).
    None when nothing is needed."""
    total = sum(count(r) for r in needed.values())
    if not total:
        return None
    shared = 0
    for path, want in needed.items():
        have = merge(held.get(path, []))
        for a, b in merge(want):
            shared += sum(max(0, min(b, d) - max(a, c) + 1) for c, d in have)
    return shared / total
