---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when the user wants to stress-test a plan, get grilled on their design, or says "grill me".
---

Interview me relentlessly about every aspect of this plan until we reach a
shared understanding. Walk down each branch of the design tree, resolving
dependencies between decisions one-by-one. For each question, provide your
recommended answer.

Ask the questions one at a time, with `ask_user`.

If a question can be answered by looking it up yourself — the workspace files,
the knowledge base, an entity that already exists — look it up instead of
asking.

---

The last rule is the one that makes this worth doing. A question you could
have answered yourself costs the user a decision they shouldn't have had to
make, and it buries the questions only they can answer.

One at a time matters for the same reason: each answer changes what the next
question should be. `ask_user` accepts several questions at once, which is
right when the answers are independent — but a decision tree is exactly the
case where they are not, so here you ask one, hear the answer, and only then
work out what to ask next.
