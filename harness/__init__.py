"""Our own harness: one tool-calling loop for every policy (step 11).

agent.py runs the loop (model calls, the policy's hooks, the budget gate, ending and logging);
interface.py is what the model sees, the same for every policy; tools.py runs the tools in the
task's container; budget.py is CLM's budget gate. A policy is the only part that differs between
arms, and the harness imports it by the name it is given: it names no policy package itself.
"""
