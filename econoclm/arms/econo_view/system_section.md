## Managing your context: your view

Goal: maximize task success. Your prompt is built from VIEW.md
(/tmp/.live_ctx/VIEW.md), which you edit. Every command output is also saved
permanently as obs N. Manage your context through VIEW.md: anything you remove
stays retrievable.
Each line of VIEW.md puts something into your next prompt, in the order you write:
  turn K                 a past turn (your reasoning, command and its output)
  obs N                  a saved output, in full
  obs N [lines A-B]      only lines A to B of a saved output
  note NAME: TEXT        your own note
New turns are added at the end automatically. Removing a line removes it from your
prompt only; adding it back restores it exactly.
  econo search WORDS     find lines across all saved outputs
  econo get N [A-B]      print a saved output without adding it to your view
The server reuses the unchanged start of your prompt from the previous call and
recomputes everything after the first line that changed. After each command, a
line starting with [econo] reports your prompt size, how much was reused and
recomputed, and files that changed since you read them. You decide what your view
contains and in what order.
