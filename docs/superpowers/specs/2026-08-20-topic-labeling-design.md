# Topic labeling: one judgement kernel for both new and historical mail

Status: approved 2026-08-20. Supersedes nothing; adds a capability alongside the existing
priority classifier.

## Why

The skill already answers "must I act on this" (`URGENT|ACTION|FYI|NOISE`). It has never
answered "what is this about". That second question was being answered by Gmail filters
written by hand, and they answered it with full text keyword matching, which fails in a
specific and reproducible way.

Three real failure shapes, all observed in a full audit of a live mailbox set:

1. A full text rule `(receipt | 收据)` labelled every message containing that token as a
   purchase receipt. It caught a hotel password mail, a "confirmation of receipt" of
   recommendation letters, a declined transaction, and cash back marketing. The same rule
   also skipped the inbox, so the misfiled mail was not merely mislabelled, it was unseen.
2. A sender list for a code hosting label contained a paper recommendation service's
   domain. Every message from that service was filed as code hosting. No amount of rule
   tuning finds this: the rule is doing exactly what it says.
3. A sender that emits several unrelated kinds of mail (security alerts, activity
   notifications, billing) got one label for all of them, because a sender level rule
   cannot express "it depends on the message".

The deeper cause is not bad rules. It is that the standard for what a label means lived in
three places at once: the filter expressions, the skill's semantic label list, and the
operator's head. Nothing reconciled them, so they drifted independently. Any design that
leaves two copies of the standard reproduces the bug.

## Invariants

These are the load bearing constraints. Everything else is implementation.

1. **One standard, in the private config.** `rules/taxonomy.md` in the companion config repo
   is the only definition of what each label means. The public repo ships no copy of it, not
   even an abridged one, because a second copy is a second thing to drift.
2. **One producer.** Topic labels come from `em_topic.judge()` and nowhere else. The
   incremental path and the batch path are both callers. Neither may reimplement judgement.
3. **One writer.** Label mutations go through a single function that records provenance.
4. **Labels only, never de-inbox.** Topic labeling never removes `\Inbox`, regardless of the
   `archive` setting. Archiving is a separate, separately gated capability.
5. **Omission over commission.** Every gate below fails toward "do not label". A message
   with no label costs the operator nothing; a wrong label is the defect being fixed.
6. **Uninitialised means inert.** With no config present the capability is off and silent,
   consistent with the skill's existing posture.

## Architecture

```
private config repo  (the signal)          public skill repo  (the method)
---------------------------------          ---------------------------------
rules/taxonomy.md        label semantics    scripts/em_topic.py     judgement kernel
rules/sender_map.json    sender -> label    scripts/em_relabel.py   batch entry point
rules/regression.jsonl   real known bugs    scripts/em_tick.py      incremental caller
state/topic_ledger.jsonl provenance         tests/                  synthetic fixtures
```

`sender_map.json` is the single source for deterministic sender to label mappings. It feeds
two consumers: the pre-gate inside `em_topic`, and the generated Gmail filter XML. Because
both are generated from one file, the filters and the skill cannot disagree.

## Component: em_topic.py

Pure judgement. No IMAP, no network beyond the model call, no writes. Testable offline.

```
judge(message, taxonomy, sender_map) -> Verdict
  Verdict = { state: "decided" | "unsure" | "failed",
              labels: [ { label, evidence, source } ],
              reason: str }
```

Pipeline, in order, first hit wins:

**Stage 1, deterministic pre-gate.** If the sender matches `sender_map.json`, emit that label
with `source="map"` and stop. If the message carries `List-Id` or `List-Unsubscribe` and the
map has a rule keyed on the list identity, same. A message resolved here never reaches the
model: it cannot be mislabelled by the model, and it costs nothing.

**Stage 2, model judgement.** Sender plus subject only, never the body. The prompt carries the
taxonomy and the account's allowed label set. Output is structured: for each proposed label,
a verbatim evidence span.

**Stage 3, evidence verification.** For each proposed label, normalise case and whitespace on
both sides and require the evidence span to be a literal substring of the From or Subject.
A label whose evidence cannot be located is dropped, and the drop is recorded. This gate
exists because a model's self reported confidence is least reliable exactly where it has
least information, while "quote something that is actually there" is far harder to fake.

**Stage 4, abstention.** Three states, not two. `decided` writes labels. `unsure` means the
model judged but did not clear the bar, or every proposed label lost its evidence check.
`failed` means the call itself broke (timeout, unparseable, unknown label, quota). Neither
`unsure` nor `failed` writes any label. They differ only in the ledger, because a persistent
`failed` rate is an outage and a persistent `unsure` rate is a taxonomy problem.

## Component: em_relabel.py

Batch entry point over historical mail. Reuses the kernel; adds no judgement of its own.

```
em_relabel.py --account <slug> [--since <date>] [--review] --dry | --commit
```

Sequence, each step verified rather than assumed:

1. Read headers over IMAP, read only, `BODY.PEEK`, anchored on All Mail.
2. Chunk by Gmail thread so one thread is never split across two judgements.
3. Judge each message through the kernel.
4. Assert completeness: one verdict per message, and for existing labels the kept set plus
   the removed set must equal exactly the message's current label set. A judgement that
   silently drops a message must fail the run, not shrink it.
5. With `--review`, re-judge every proposed change with an independent pass that is not shown
   the first pass's reasoning, and keep only changes both passes agree on. Measured on the
   2026-08 run: this rejected 303 of 1245 proposed removals, and independently sampled
   verification later confirmed it had caught 5 of 6 known bad removals.
6. Write the rollback snapshot of every touched message's prior label set BEFORE any write.
7. Apply, grouped by label, batched under 1000 per operation, located by `X-GM-MSGID`.
8. Read back every touched message and assert its label set equals the expected set exactly.

## Component: filter generation

`sender_map.json` compiles to Gmail filter XML. The filters keep the job they are actually
good at: deterministic sender to label mapping, applied by Google, working on mobile, working
when this machine is off. They lose the job they are bad at: guessing a topic from keywords.

Rules to delete on first application, all three being full text keyword matches that also
skip the inbox: a venue name list mapping to the academic label, a `(receipt | ...)` rule
mapping to the receipt label, and a project code list mapping to a finance label. Rules to
correct: remove a paper recommendation domain from the code hosting sender list, and remove
a general purpose vendor address from the academic sender list.

Note the inbox consequence and make it explicit to the operator before applying: deleting a
rule that skipped the inbox means messages it used to hide will now arrive in the inbox.
That matches the standing instruction that every message is to be seen, but it changes inbox
volume, so it is a decision, not a side effect.

## Data classes

Per `.dataclass.json` and Skill Repo Spec s9:

- `scripts/em_topic.py`, `scripts/em_relabel.py`, this document: **TOOL**, public.
- `tests/` cases: **FIXTURE**, public, synthetic, generated by `tools/make_fixtures.py`.
  The regression cases in the public repo reproduce the failure *shapes* using
  `example.com` style senders. They must never be real messages. This repo already leaked
  once by pasting real mail into a golden file; the generator equality check exists to make
  that fail loudly rather than silently.
- `rules/taxonomy.md`, `rules/sender_map.json`, `rules/regression.jsonl`,
  `state/topic_ledger.jsonl`: **DATA**, private companion config only. The taxonomy names
  real institutions the operator deals with, which is exactly the kind of prose no structural
  scanner will catch, so its class is declared rather than inferred.

## Testing

Every gate gets a test that can fail:

- Evidence verification: feed a verdict whose evidence span does not occur in the input. The
  label must be dropped. This is the negative control for the whole gate; without it a
  broken check and a clean run look identical.
- Pre-gate: a mapped sender must never reach the model. Assert on the model call count, not
  on the output.
- Abstention: a malformed model response must yield `failed` and write nothing. An
  in-taxonomy but low evidence response must yield `unsure` and write nothing.
- Completeness: a batch whose verdict file is short by one message must fail the run.
- Synthetic regression: the four known failure shapes, rebuilt with fictional senders.

## Deferred

Recorded so they are visible rather than forgotten, not scheduled:

- Provenance ledger with per batch rollback and prompt versioned cache keys.
- Frozen regression set gating every prompt change, with a deliberate ambiguous stratum,
  because a set containing only known bugs will always pass.
- Novelty gate: distance from the candidate message to the confirmed members of a label,
  which is the only mechanism that structurally catches the "wrong domain entirely" class.
  A single confidence threshold cannot: out of distribution samples can score more
  confidently than in distribution ones.
- Stratified audit with exact binomial intervals to put a falsifiable number on precision.
