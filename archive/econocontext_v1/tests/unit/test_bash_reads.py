"""Shell commands as reads: the command shapes Claude Code's workers used in wctl4/wres4."""

import os

from econocontext.costmodel.bash_reads import (LineRead, line_overlap, merge, resolve,
                                                shell_reads)


def ws(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("".join(f"line {i}\n" for i in range(1, 301)))
    (tmp_path / "pkg" / "b.py").write_text("x\n" * 50)
    return str(tmp_path)


def reads(command, root, output="", roots=None, cwd=None):
    ok, found = shell_reads(command, output, cwd or root, roots)
    return ok, [(os.path.relpath(r.path, root), r.start, r.end) for r in found]


def test_sed_cat_head_tail_and_awk(tmp_path):
    root = ws(tmp_path)
    assert reads("sed -n '10,20p' pkg/a.py", root) == (True, [("pkg/a.py", 10, 20)])
    assert reads("sed -n '5p;40,$p' pkg/a.py", root) == (True, [("pkg/a.py", 5, 5), ("pkg/a.py", 40, None)])
    assert reads("cat pkg/a.py pkg/b.py", root) == (True, [("pkg/a.py", 1, None), ("pkg/b.py", 1, None)])
    assert reads("nl -ba pkg/b.py", root) == (True, [("pkg/b.py", 1, None)])
    assert reads("head -n 30 pkg/a.py", root) == (True, [("pkg/a.py", 1, 30)])
    assert reads("head -30 pkg/a.py", root) == (True, [("pkg/a.py", 1, 30)])
    assert reads("tail -n 20 pkg/a.py", root) == (True, [("pkg/a.py", -20, None)])
    assert reads("tail -n +100 pkg/a.py", root) == (True, [("pkg/a.py", 100, None)])
    assert reads("awk 'NR>=7 && NR<=9' pkg/a.py", root) == (True, [("pkg/a.py", 7, 9)])


def test_scripts_with_cd_variables_echo_and_comments(tmp_path):
    root = ws(tmp_path)
    script = ("cd " + root + "\n# look at the filter\necho \"=== filter ===\"\n"
              "F=" + root + "/pkg/a.py\nsed -n '600,700p' $F 2>/dev/null\nsed -n \"1,5p\" \"${F}\"")
    assert reads(script, root, cwd="/elsewhere") == (True, [("pkg/a.py", 600, 700), ("pkg/a.py", 1, 5)])
    assert reads("cd pkg && cat b.py", root) == (True, [("pkg/b.py", 1, None)])


def test_pipes_keep_head_and_drop_filters(tmp_path):
    root = ws(tmp_path)
    assert reads("sed -n '1,200p' pkg/a.py | head -n 50", root) == (True, [("pkg/a.py", 1, 50)])
    assert reads("cat pkg/a.py | grep line", root) == (True, [])  # lines shown without numbers


def test_docker_exec_and_bash_c_are_unwrapped(tmp_path):
    root = ws(tmp_path)
    cmd = "docker exec -w /testbed c1 bash -lc 'source /opt/x && sed -n \"3,4p\" pkg/a.py'"
    assert reads(cmd, root, roots={"/testbed": root}) == (True, [("pkg/a.py", 3, 4)])
    assert reads("bash -c 'cat /testbed/pkg/b.py'", root, roots={"/testbed": root}) == (True, [("pkg/b.py", 1, None)])


def test_grep_n_lines_come_from_the_output(tmp_path):
    root = ws(tmp_path)
    out = "pkg/a.py:12:line 12\npkg/a.py-13-line 13\n--\npkg/b.py:3:x\nnot/a/file.py:9:z\n"
    assert reads("grep -rn -A 1 'line 12\\|x' pkg | head -30", root, out) == (
        True, [("pkg/a.py", 12, 12), ("pkg/a.py", 13, 13), ("pkg/b.py", 3, 3)])
    # every grep read the same one file: bare numbers are its lines
    two = "grep -n foo pkg/a.py\necho ---\ngrep -n bar pkg/a.py"
    assert reads(two, root, "4:foo\n---\n7:bar\n") == (True, [("pkg/a.py", 4, 4), ("pkg/a.py", 7, 7)])
    assert reads("grep foo pkg/a.py", root, "4:foo\n")[1] == []  # no -n: no line numbers
    # a hit whose text looks like "a-1-b" is still a bare line of the one file
    assert reads("grep -n x pkg/a.py | head -3", root, "12:    x = a-1-b\n")[1] == [("pkg/a.py", 12, 12)]


def test_anything_else_is_not_read_only(tmp_path):
    root = ws(tmp_path)
    for cmd in ("python -m pytest", "sed -i 's/a/b/' pkg/a.py", "mkdir -p docs",
                "cat pkg/a.py > out.txt", "find . -name '*.pyc' -delete", "git checkout x",
                "docker exec -w /testbed c1 bash -lc 'python -m pytest'", "echo 'unbalanced"):
        assert reads(cmd, root)[0] is False, cmd
    for cmd in ("ls -la", "wc -l pkg/a.py", "find . -name '*.py' | grep -v test",
                "git diff HEAD", "grep -n x pkg/a.py 2>&1 | head -5"):
        assert reads(cmd, root)[0] is True, cmd


def test_ranges_resolve_merge_and_overlap():
    assert resolve(LineRead("f", -20, None), 300) == (281, 300)
    assert resolve(LineRead("f", 1, None), 50) == (1, 50)
    assert resolve(LineRead("f", 60, 80), 50) is None
    assert merge([(10, 20), (21, 25), (1, 3), (15, 18)]) == [(1, 3), (10, 25)]
    held = {"a.py": [(1, 100)], "b.py": [(1, 10)]}
    assert line_overlap(held, {"a.py": [(51, 150)]}) == 0.5
    assert line_overlap(held, {"c.py": [(1, 10)]}) == 0.0
    assert line_overlap(held, {}) is None
