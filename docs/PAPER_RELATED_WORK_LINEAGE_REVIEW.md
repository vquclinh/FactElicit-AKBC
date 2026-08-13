# COVER-KBC Related Work and Design-Lineage Review

Review metadata:

- Review date: 2026-08-13
- Repository HEAD: `991bca5640723a24d84c1d41a174e8d1d3dceed5`
- Starting worktree status: already dirty before this review; this review creates only this file.
- Mandatory proposal file requested by user: `docs/COVER_KBC_Technical_Proposal_New.pdf`
- Actual proposal path found and read: `./COVER_KBC_Technical_Proposal_New.pdf`
- Proposal PDF size: 26 pages, about 11,080 extracted words by `pdftotext -layout`
- Hidden TEST inference: not run
- Expensive neural inference: not run

The requested proposal path under `docs/` does not exist in this checkout. The
same-named PDF exists at repository root. This review treats the root-level PDF
as the mandatory Technical Proposal source and records the path discrepancy.

## 1. Review Scope

This second review answers a different question from the first related-work
dossier:

```text
Which research lines actually shaped COVER-KBC's design,
and what did COVER-KBC retain, change, combine, or operationalize?
```

The previous review correctly identified many LM-KBC challenge systems, but
that perspective is too benchmark-centric for the final six-page paper. The
Technical Proposal shows that COVER-KBC was designed from a broader lineage:

- prompt programs and prompt optimization,
- closed-book parametric knowledge elicitation,
- RAG-style query rewriting and active retrieval control, but without retrieval,
- self-consistency and atomic answer merging,
- blind verification and verifier bias control,
- contextual calibration,
- residual coverage and completeness/missingness concepts,
- adaptive test-time compute,
- action/value scheduling,
- relation-specific final selection.

Source priority for this review:

1. `./COVER_KBC_Technical_Proposal_New.pdf`
2. executable source in `src/cover_kbc/`
3. module/audit lineage in `docs/audits/`
4. current paper docs: `docs/PAPER_SYSTEM_SUMMARY.md`, `docs/PAPER_FULL_ARCHITECTURE_REVIEW.md`, `docs/PAPER_RELATED_WORK_REVIEW.md`, and `docs/IMPLEMENTATION_STATUS.md`
5. external primary paper metadata from ACL Anthology, CEUR, PMLR, ICLR/NeurIPS/ICML proceedings pages, ACM/DBLP, and publisher pages.

No source code, config, existing documentation, notebook, script, benchmark
file, or git history was modified. The only intended worktree change is this
Markdown file.

## 2. Technical Proposal Reference Extraction

The Technical Proposal explicitly frames COVER-KBC as:

```text
Coverage-guided Open-set Verification and Elicitation
for Relation-wise Knowledge Base Construction
```

Its central thesis is that each subject-relation pair should be treated as a
specialist-routed active set discovery process: compile the relation into a
prompt program, probe frozen parametric memory through structurally independent
branches, store atomic evidence, measure uncertainty from logits and
disagreement, estimate what remains undiscovered, and allocate test-time
compute until expected marginal benefit becomes too small.

The proposal references 43 numbered items. They are not all equal. Some are
direct method inspirations; others are task/model context or optional future
work. The table below extracts each referenced paper, method, framework, or
named research idea and maps it to COVER-KBC.

| Ref | Paper / method | Authors, year, venue | Proposal role | COVER-KBC component influenced | Survived into current E3? |
| --- | --- | --- | --- | --- | --- |
| [1] | AKBC Shared Task 2026 task page | AKBC organizers, 2026 | Benchmark/rule context | closed-book rule, 32B budget, six relations, no external factual sources | ACTIVE context |
| [2] | ReWiSe: Relation-Wise Self-consistency for LLM Probing | Albert-Roulhac and Zouaq, 2025, CEUR LM-KBC | LM-KBC relation-wise self-consistency precedent | relation-wise aggregation, repeated probing | POST-HOC/benchmark context; not direct implementation source except conceptual comparison |
| [3] | Combining Self-Retrieval-Augmented Generation with Divide-and-Conquer for Language Model-based KBC | He and Razniewski, 2025, CEUR LM-KBC | LM-KBC relation-dependent decomposition precedent | award divide-and-conquer, internal-description/self-retrieval analogy | POST-HOC close system; not copied into E3 |
| [4] | Self-Consistency Improves Chain of Thought Reasoning in Language Models | Wang et al., 2023, ICLR | Direct consensus precedent | multiple sampled/recalled paths and answer agreement | ADAPTED in core evidence/consensus and final numeric Multi-View |
| [5] | Atomic Self-Consistency for Better Long Form Generations | Thirukovalluru, Huang, Dhingra, 2024, EMNLP | Direct atomic merging precedent | M16 atomic consensus; merge subparts rather than choose one whole answer | SHADOW/diagnostic plus design lineage |
| [6] | Chain-of-Verification Reduces Hallucination in Large Language Models | Dhuliawala et al., 2024, Findings ACL | Direct blind verification precedent | M4 verifier, M17 verifier, do not pass generator reasoning to verifier | ACTIVE core and shadow specialist verifier |
| [7] | Let's Sample Step by Step: Adaptive-Consistency for Efficient Reasoning and Coding with LLMs | Aggarwal et al., 2023, EMNLP | Direct adaptive budget/stopping precedent | M20/M21, adaptive call spending, stop when agreement/search value is enough | ACTIVE/planning and conceptual lineage |
| [8] | SELF-DISCOVER: Large Language Models Self-Compose Reasoning Structures | Zhou et al., 2024, NeurIPS | Structured reasoning-program precedent | relation/task-specific reasoning structures | SHADOW prompt/specialist design; not as self-composition runtime |
| [9] | ReAct: Synergizing Reasoning and Acting in Language Models | Yao et al., 2023, ICLR | Agentic loop precedent | state/action/observation framing | GENERIC conceptual precedent; COVER has no external environment |
| [10] | Tree of Thoughts: Deliberate Problem Solving with Large Language Models | Yao et al., 2023, NeurIPS | Search/deliberation precedent | branching and self-evaluation in action space | CONCEPTUAL only; COVER does bounded deterministic control, not ToT |
| [11] | Language Agent Tree Search Unifies Reasoning, Acting, and Planning in Language Models | Zhou et al., 2024, ICML | Planning/search precedent | M21 micro-lookahead | CONCEPTUAL; COVER uses small rule-based action utility, not MCTS |
| [12] | Take a Step Back: Evoking Reasoning via Abstraction in Large Language Models | Zheng et al., 2024, ICLR | Direct step-back abstraction precedent | M10 query specification; Area/Capacity step-back semantic views | ACTIVE in final numeric views and shadow M10 |
| [13] | Large Language Models Are Human-Level Prompt Engineers / APE | Zhou et al., 2023, ICLR | Direct prompt-as-program/search precedent | Prompt Program Compiler, offline prompt variants | SHADOW/diagnostic; E3 prompts are hand-frozen |
| [14] | Large Language Models as Optimizers / OPRO | Yang et al., 2024, ICLR | Natural-language optimization precedent | Prompt Lab concept, prompt history and score feedback | HISTORICAL/proposal only; no active OPRO optimizer |
| [15] | Optimizing Instructions and Demonstrations for Multi-Stage Language Model Programs / MIPRO | Opsahl-Ong et al., 2024, EMNLP | Direct language-model-program precedent | prompt programs across modular LM calls | SHADOW M10 design; no learned/surrogate optimizer active |
| [16] | AMPO: Automatic Multi-Branched Prompt Optimization | Sheng Yang et al., 2024, EMNLP | Multi-branch prompt optimization precedent | relation branches and failure-pattern prompt families | SHADOW/proposal lineage |
| [17] | Black-Box Prompt Optimization: Aligning LLMs without Model Training / BPO | Cheng et al., 2024, ACL | Training-free prompt optimization precedent | no-weight-update optimization boundary | PROPOSAL lineage; not active optimizer |
| [18] | Prompt Optimization via Adversarial In-Context Learning | Do et al., 2024, ACL | Prompt improvement without weight updates | prompt lab and adversarial prompt variants | PROPOSAL lineage only |
| [19] | Calibrate Before Use | Zhao et al., 2021, ICML | Direct contextual calibration precedent | M4 content-free verifier-label calibration | ACTIVE core |
| [20] | DoLa: Decoding by Contrasting Layers Improves Factuality | Chuang et al., 2024, ICLR | Optional factual decoding adapter | hidden-states seam, optional DoLa | NEVER ACTIVATED / deferred |
| [21] | SELF-RAG | Asai et al., 2024, ICLR | RAG control and critique inspiration | retrieve/critique/control analogy without external retrieval | CONCEPTUAL; external RAG disabled |
| [22] | HyDE: Precise Zero-Shot Dense Retrieval without Relevance Labels | Gao et al., 2023, ACL | Pseudo-document/query expansion precedent | pseudo-memory/parametric retrieval idea | SHADOW M11; no external retrieval |
| [23] | Query2Doc | Wang, Yang, Wei, 2023, EMNLP | LLM-generated pseudo-document expansion | pseudo-memory sketch | SHADOW M11 |
| [24] | Rewrite-Retrieve-Read / Query Rewriting in RAG LLMs | Ma et al., 2023, EMNLP | query rewriting precedent | M10/M11 query rewrite and negative anchors | SHADOW M10/M11; no retriever |
| [25] | Active Retrieval Augmented Generation / FLARE | Jiang et al., 2023, EMNLP | adaptive retrieval trigger precedent | low-confidence/uncertainty-driven ask-more policy | ACTIVE/SHADOW control analogy; no retrieval |
| [26] | LLatrieval: LLM-Verified Retrieval for Verifiable Generation | Li et al., 2024, NAACL | verify-before-trust retrieval precedent | verification before accepting generated evidence | CONCEPTUAL; no retrieval |
| [27] | Measuring and Narrowing the Compositionality Gap / Self-Ask | Press et al., 2023, Findings EMNLP | direct self-ask decomposition precedent | M11 self-ask probe family | SHADOW M11 |
| [28] | Language Models Hallucinate, but May Excel at Fact Verification | Guan et al., 2024, NAACL | verifier role separation precedent | model-role bakeoff: generator quality != verifier quality | ACTIVE design principle; E3 uses same Mistral runtime |
| [29] | Large Language Models are not Fair Evaluators | Wang et al., 2024, ACL | evaluator/judge bias warning | template-order/label-order bias controls | SHADOW diagnostics and M4 caution |
| [30] | Large Language Models Cannot Self-Correct Reasoning Yet | Huang et al., 2024, ICLR | warning against generic self-correction | M18 avoids "think again" prompts | SHADOW M18 and design principle |
| [31] | Large Language Models Can Self-Correct with Key Condition Verification / ProCo | Wu et al., 2024, EMNLP | targeted verification precedent | M18 key-condition reconstruction | SHADOW M18 |
| [32] | Inference Scaling Laws | Yangzhen Wu et al., 2025, ICLR | test-time compute scaling precedent | compute-optimal inference framing | CONCEPTUAL for M20/M21 |
| [33] | Scaling LLM Test-Time Compute Optimally Can be More Effective than Scaling Parameters | Snell et al., 2025, ICLR | adaptive test-time compute precedent | M20/M21 budget scheduling | CONCEPTUAL for paper positioning |
| [34] | Empower Entity Set Expansion via Language Model Probing | Zhang et al., 2020, ACL | semantic-class drift control precedent | positive/negative class names, hard negatives, award expansion | CONCEPTUAL for set-valued relations |
| [35] | Large Language Models are Effective Text Rankers with Pairwise Ranking Prompting | Qin et al., 2024, Findings NAACL | pairwise burden-reduction precedent | numeric contrastive judge idea | ACTIVE in E3 spirit, not exact PRP |
| [36] | Mistral-Small-3.2 model card | Mistral AI, 2025 | model context | active checkpoint | ACTIVE E3 model |
| [37] | Qwen3-30B-A3B model card | Qwen Team | model bakeoff context | possible legal model profile | NEVER ACTIVE in E3 |
| [38] | Qwen3-14B model card | Qwen Team | model bakeoff context | possible cross-family pairing | NEVER ACTIVE in E3 |
| [39] | Phi-4 model card | Microsoft | model bakeoff context | possible cross-family pairing | NEVER ACTIVE in E3 |
| [40] | Qwen3.6-27B model card | Qwen Team, 2026 | model bakeoff context | possible freshness profile | NEVER ACTIVE in E3 |
| [41] | Qwen3.5-27B model card | Qwen Team, 2026 | model bakeoff context | possible freshness profile | NEVER ACTIVE in E3 |
| [42] | Gemma 3 27B model card | Google DeepMind | model bakeoff context | possible alternative family | NEVER ACTIVE in E3 |
| [43] | Phi-4-mini-instruct model card | Microsoft | model bakeoff context | possible verifier alternative | NEVER ACTIVE in E3 |

Important extraction finding: the proposal itself says the final paper should
not claim that every block is novel. It recommends claiming only the core
abstractions: relation-typed active evidence acquisition, independence-aware
calibrated atomic evidence, and coverage/uncertainty-guided test-time control.
This review agrees, but adds a caveat from the implementation: several M9-M21
components are shadow/diagnostic or production-gated, while Profile E3's hidden
score gains concentrate in the conservative final specialization layer.

## 3. Repository Design-Lineage Evidence

Repository design-lineage evidence is unusually strong because the audits
quote proposal sections and record implementation status. A repository-wide
search found 142 files containing proposal/module/citation-related lineage
terms across `docs/audits`, `src/cover_kbc`, tests, and configs.

Most important local design-lineage references:

| Evidence source | What it proves |
| --- | --- |
| `docs/audits/0001-milestone-1-foundation.md` | M0-M3 and initial evidence graph began as relation contracts, typed routing, diverse elicitation, normalization, provenance, and model-budget infrastructure. RCSE/control were explicitly deferred. |
| `docs/audits/0002-milestone-2-architecture.md` | M4-M8 were implemented: blind verifier, contextual calibration, scoring, RCSE, controller, relation-specific selector, staged execution. |
| `docs/audits/0009-module-6-rcse-conformance.md` | RCSE is not Chao/capture-recapture; it estimates residual search need, not hidden cardinality. It separates candidate coverage `q(o)` from query residual `q_res`. |
| `docs/audits/0017-module-10-prompt-program-compiler-conformance.md` | M10 implements typed prompt programs as structured blueprints, not model calls. Proposal fields that belonged to M2/M4/M11 were deliberately not duplicated. |
| `docs/audits/0018-module-11-closed-book-parametric-retrieval-conformance.md` | M11 implements pseudo-memory, self-ask, and query-rewrite probes, explicitly without external retrieval and without turning generated prose into truth evidence. |
| `docs/audits/0023-module-16-atomic-consensus-engine-conformance.md` | M16 directly maps Self-Consistency/ASC into an atomic candidate graph, using `q_g = max` per independence group and feature vector `phi(o)`. |
| `docs/audits/0026-module-18-bidirectional-counterfactual-verification-conformance.md` | M18 implements reverse, key-condition, counterfactual, and candidate-free recall checks as new evidence rather than generic self-correction. |
| `docs/audits/0028-module-19-coverage-gap-missingness-conformance.md` | M19 implements proposal `R_t` as a uniform, unfitted, non-probabilistic residual search-thinness index; shadow only. |
| `docs/audits/0047-offline-m20-m21-calibration-derivation.md` | M20/M21 are derived from TRAIN telemetry as deterministic budget/action estimates, not trained neural policy; real TRAIN derivation was not initially run. |
| `docs/audits/0073-v3-calibration-derivation-and-production-readiness.md` | V3 M21 calibration later used observed V3 action effects and fallback bins; M20 inherited frozen V2 budget. |
| `docs/audits/0082-v3-2-leaderboard-repair-stack.md` | Leaderboard repair stack was added downstream as L7-L9 repair, not as the whole architecture. |
| `docs/audits/0091-profile-e3-area-multiview-baseline-promotion.md` | Current E3 gain is controlled Area Multi-View on top of E2; non-Area relations unchanged. |
| `src/cover_kbc/contracts/base.py` | Relation contracts are executable semantics consumed by router, elicitation, parser, verifier, selector, and controller. |
| `src/cover_kbc/evidence/graph.py` | Evidence graph preserves candidate identity, signed evidence edges, independence groups, provenance, verifier results, RCSE state, and budgets. |
| `src/cover_kbc/verification/blind.py` | Blind verifier is label-scored and contextually calibrated; it does not see generator reasoning. |
| `src/cover_kbc/scoring.py` | Candidate scoring separates support, verifier log-odds, cross-model evidence, contradictions, and disagreement. |
| `src/cover_kbc/coverage.py` | RCSE formulae are active core and explicitly distinguish search need from correctness confidence. |
| `src/cover_kbc/controller.py` | Controller implements a deterministic action loop, legal actions, action score, budgets, and relation-specific stopping. |
| `src/cover_kbc/selection.py` | Final selection is relation-program-specific: small set, null-single, large-open-set, numeric. |
| `src/cover_kbc/control/micro_planner.py` | M21 expected-value utility is implemented as a separate planner over legal/affordable actions. |
| `docs/IMPLEMENTATION_STATUS.md` | E3 active model and final specialization state: one Mistral checkpoint, Qwen inactive, Area/Capacity Multi-View active. |

Design-lineage conclusion:

```text
M0-M8 are the active architectural core.
M9-M19 are mostly shadow/diagnostic but preserve methodological ideas.
M20-M21/V3 are production-gated/planning components.
Profile E3 final specialization supplies the current hidden-best score.
```

The Related Work should therefore cite papers that shaped the full design, but
the Results/Ablation discussion must be honest that final E3's measured hidden
improvement is dominated by conservative relation-specific specialization.

## 4. Active COVER-KBC Components

### ACTIVE CORE

These components are current architecture, not just proposal text:

- M0 relation contracts: relation semantics, output type, cardinality, hard negatives, view families, verification policy, stopping policy, selection policy.
- M1 typed router: maps `(subject, relation)` to a typed program regime.
- M2 diverse elicitation: contract-bound generation views.
- M3 evidence graph: candidate identity, signed evidence, provenance, independence groups.
- M4 blind verifier: A/B/C label scoring, contextual calibration, entropy/margin/disagreement.
- M5 scoring: `S(o) = alpha F + beta L + gamma X - delta C - eta U`.
- M6 RCSE: residual search need, not candidate confidence and not cardinality.
- M7 controller: legal actions, budget, action utility, relation-specific stopping.
- M8 selector: relation-aware finalization.

### ACTIVE FINAL SPECIALIZATION

Profile E3 adds:

- AwardMetadataNormalizer for deterministic wrapper cleanup and dedupe.
- MistralCityEmptyRescue for empty city-of-death rows only.
- MistralCapacityMultiView for all capacity rows.
- MistralAreaMultiView for all area rows.
- no E3 stock repair.
- no E3 border repair.

### SHADOW / DIAGNOSTIC

Methodologically important but not all score-driving in E3:

- M9 query intelligence and risk profile.
- M10 prompt program compiler.
- M11 closed-book parametric retrieval.
- M12-M15 specialists.
- M16 atomic consensus.
- M17 specialist verifier suite.
- M18 bidirectional/counterfactual checks.
- M19 coverage gap/missingness.
- M20 relation budget scheduler.
- M21 expected-value micro-planner.

### HISTORICAL / RETIRED / NEVER IMPLEMENTED

- Qwen verifier/cross-family pairing: historical, inactive in E3.
- DoLa: proposal optional adapter, never activated.
- OPRO/MIPRO/AMPO/BPO-style automatic prompt search: design inspiration, not an active optimizer.
- C2, CHIV, NSMV, direct border repair, broad numeric repairs: historical negative probes.

## 5. Component-to-Paper Lineage Map

| COVER-KBC component | Paper | Relation to our design | Inspiration type | What we retain | What we change/add | Current implementation status | Should cite in final paper? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Closed-book parametric KBC | Petroni et al. 2019, LAMA | Establishes pretrained LMs as relational knowledge stores | IMPORTANT CONCEPTUAL PRECEDENT | probing parametric knowledge | actual KB row construction, not cloze probing | ACTIVE setting | SHOULD |
| LM-KBC benchmark setting | Kalo et al. 2025 overview | Defines recent task context and relation family | BENCHMARK CONTEXT | closed-book KBC, six relation types | 2026 budget/model differs | ACTIVE context | MUST |
| Relation-wise strategy | ReWiSe 2025 | Prior LM-KBC relation-wise self-consistency | POST-HOC RELATED WORK | relation-wise aggregation | evidence graph, verification, control, selectors | not implementation source | SHOULD |
| Relation-dependent hybrid KBC | He and Razniewski 2025 | Closest benchmark-level hybrid | POST-HOC RELATED WORK | relation-dependent decomposition | executable contracts and evidence/control state | not implementation source | MUST/SHOULD |
| Relation-typed inference programs | MIPRO 2024; SELF-DISCOVER 2024; ReAct 2023 | Language model programs and reasoning/action structure | DIRECT/CONCEPTUAL | modular LM-call pipelines; state/action vocabulary | fixed relation contracts; no learned router; no external environment | ACTIVE core plus shadow M10/M21 | SHOULD cite MIPRO or SELF-DISCOVER, not all |
| Prompt program compiler | APE 2023; OPRO 2024; MIPRO 2024; AMPO 2024; BPO 2024 | Prompt-as-program and optimization lineage | DIRECT DESIGN INSPIRATION | prompt variants and module-aware prompt programs | discrete, frozen, no active optimizer in E3 | SHADOW | OPTIONAL/APPENDIX |
| Step-back query specification | Step-Back Prompting 2024 | Abstract before answering | DIRECT DESIGN INSPIRATION | high-level semantic specification | in COVER, step-back produces search/answer constraints, not free reasoning | ACTIVE in Area/Capacity views; shadow M10 | SHOULD if space |
| Self-ask decomposition | Press et al. 2023 | Explicit follow-up decomposition | DIRECT DESIGN INSPIRATION | decomposition into subquestions | no search engine; frozen model only | SHADOW M11 | OPTIONAL |
| Parametric retrieval without retrieval | HyDE 2023; Query2Doc 2023; Rewrite-Retrieve-Read 2023; FLARE 2023; Self-RAG 2024 | RAG control ideas transformed into closed-book prompts | DIRECT CONCEPTUAL INSPIRATION | query rewrite, pseudo-doc, active retrieval trigger, verify-before-trust | no external corpus, pseudo-context is not evidence | SHADOW M11; active design constraint | SHOULD cite FLARE or Self-RAG plus maybe HyDE/Query2Doc |
| Multi-view elicitation | Self-Consistency 2023 | Multiple paths and answer agreement | DIRECT DESIGN INSPIRATION | diversity/consensus | semantic independence groups; evidence graph; no CoT transfer to verifier | ACTIVE/SHADOW and E3 numeric | MUST |
| Atomic evidence merging | Atomic Self-Consistency 2024 | Merging subparts instead of selecting one generation | DIRECT DESIGN INSPIRATION | atomic merge principle | relation-aware candidate graph with provenance and signed evidence | SHADOW M16, core spirit | MUST/SHOULD |
| Provenance-aware evidence graph | Knowledge Vault 2014; truth discovery/source-dependence literature | Probabilistic fact fusion and source reliability lineage | CONCEPTUAL PRECEDENT | fact evidence, source/provenance, calibrated correctness | ephemeral per-query LLM evidence; no trained fusion model or web sources | ACTIVE core graph | SHOULD cite Knowledge Vault or truth-discovery survey |
| Independence groups | Truth discovery with source dependence, Dong et al. 2009 | Avoid duplicate/correlated evidence overcount | CONCEPTUAL PRECEDENT | dependence matters when fusing claims | prompt/view independence groups instead of data-source copying models | ACTIVE graph/scoring; SHADOW M16 | OPTIONAL unless space |
| Blind verification | CoVe 2024; Guan et al. 2024 | Verification separated from generation and generator reasoning | DIRECT DESIGN INSPIRATION | independent verification questions; verifier can be stronger than generator | fixed label-scored candidate verifier with calibration | ACTIVE M4; SHADOW M17/M18 | MUST |
| Verifier bias control | Calibrate Before Use 2021; LLMs are not Fair Evaluators 2024 | label/order/template bias concerns | DIRECT/CONCEPTUAL | content-free calibration; bias diagnostics | per-relation A/B/C label calibration; prompt disagreement | ACTIVE M4, shadow diagnostics | MUST cite Zhao; optional Wang |
| Candidate scoring | Knowledge Vault 2014; truth discovery survey | Separate correctness/support/conflict signals | CONCEPTUAL PRECEDENT | multi-signal fact scoring | deterministic unfitted score, no trained fusion | ACTIVE M5 | SHOULD if space |
| Candidate confidence vs answer-space coverage | Razniewski et al. 2024 completeness survey | Completeness/recall distinct from correctness | IMPORTANT CONCEPTUAL PRECEDENT | incompleteness as separate KB quality issue | test-time residual search need, not formal completeness probability | ACTIVE M6, SHADOW M19 | MUST/SHOULD |
| Adaptive control | Adaptive-Consistency 2023; FLARE 2023; Snell et al. 2025; Wu et al. 2025 | non-uniform test-time compute | DIRECT DESIGN INSPIRATION | spend more when uncertainty remains; stop early when enough | relation-typed actions and residual coverage, closed-book | ACTIVE M7 and M20/M21 planning | MUST cite Adaptive-Consistency; SHOULD cite test-time compute |
| Micro-planner | LATS 2024; ToT 2023; ReAct 2023 | action/value/search lineage | CONCEPTUAL PRECEDENT | action search and value scoring | bounded deterministic utility, no MCTS, no external environment | ACTIVE/SHADOW planning | OPTIONAL |
| Relation-aware finalization | ReWiSe 2025; LM-KBC systems | relation-specific aggregation | POST-HOC RELATED WORK | numeric median/majority, relation-wise rules | typed selector families plus mutation-bounded E3 final layer | ACTIVE M8/E3 | SHOULD |
| Numeric consensus | Self-Consistency 2023; ReWiSe 2025; pairwise ranking prompting 2024 | consensus and pairwise burden reduction | ADAPTED COMPONENT | multiple values, median/cluster/judge | semantic views, strict parsers, 5 percent clustering, source-blind judge | ACTIVE E3 Area/Capacity | SHOULD in method/ablations, maybe not Related Work |
| Conservative repair | selective prediction/cascades broad literature | gated mutation idea | SYSTEM-SPECIFIC OPERATIONALIZATION | narrow repair preconditions and fallbacks | explicit mutation boundaries per relation | ACTIVE E3 | OPTIONAL; probably explain without citation |

## 6. Relation-Typed Inference Literature

COVER-KBC's relation contracts are more than configuration. A contract is the
executable semantic interface for a relation:

```text
relation r
  -> ProgramType
  -> output type and cardinality
  -> positive semantics and hard negatives
  -> eligible view families
  -> parser/normalizer
  -> verifier policy
  -> stopping policy
  -> selector
```

Relevant lineage:

- LAMA and LM probing established that pretrained models store relational
  knowledge and can be queried through relation-specific prompts.
- LM-KBC systems such as ReWiSe and Self-RAG+DaC show that relation-wise
  strategies matter for this benchmark.
- MIPRO and the broader language-model-program literature are closer to the
  implementation idea of modular LM-call pipelines, but MIPRO optimizes prompts
  across LM programs; COVER-KBC hard-codes relation contracts and avoids a
  learned optimizer in the current system.
- SELF-DISCOVER is a broad precedent for selecting task-intrinsic reasoning
  structures. COVER-KBC does not ask the model to self-compose reasoning
  structures at runtime; it uses deterministic relation contracts.
- ReAct and LATS provide action/state/planning vocabulary, but COVER's actions
  are internal closed-book elicitation/verification actions rather than external
  tool or environment actions.

Safe distinction:

```text
COVER-KBC operationalizes schema-aware prompting as executable relation
contracts: each relation selects not just a prompt, but an elicitation,
verification, stopping, and finalization program.
```

Dangerous claim:

```text
Do not claim COVER-KBC is the first relation-aware KBC or first modular
prompting system. The safe claim is the combination and executable
operationalization for closed-book LM-KBC.
```

## 7. Evidence and Provenance Literature

COVER-KBC's evidence graph is not just a list of generated strings. It stores:

- candidate nodes keyed by relation-specific identity,
- support, contradiction, unknown, and verifier edges,
- independence group,
- model role/family,
- prompt/view id,
- operation id,
- record id,
- token/call cost,
- gate/RCSE/controller side state.

The direct proposal lineage is Self-Consistency and Atomic Self-Consistency,
but these do not fully explain provenance. Broader knowledge-fusion and truth
discovery literature is a better lineage for why provenance and dependence
matter.

Closest useful precedents:

- Knowledge Vault combines extractions from multiple sources and estimates
  calibrated fact correctness probabilities. COVER-KBC retains the idea that
  fact correctness can be represented as evidence/fusion rather than as one
  surface output. It changes the setting: evidence is ephemeral, generated by
  one frozen LM through prompt mechanisms, and not learned from web sources.
- Truth discovery/source-dependence work argues that copying/correlation can
  make naive voting overcount evidence. COVER-KBC adapts this by treating
  repeated same-view outputs as one independence group: repetitions are
  observations, not independent mechanisms.
- Atomic Self-Consistency is the closest LLM-generation precedent: merge
  authentic subparts rather than choose one whole generation. COVER-KBC turns
  this into candidate-level graph state with signed evidence and downstream
  controller features.

Safe distinction:

```text
COVER-KBC adapts knowledge-fusion and atomic self-consistency ideas to a
closed-book LLM setting by treating prompt/view provenance as a first-class
evidence dimension.
```

Do not cite graph-of-thought work merely because we can draw a graph. The
evidence graph is about provenance and fusion, not graph search over thoughts.

## 8. Verification and Calibration Literature

COVER-KBC's verifier is deliberately blind:

```text
input: subject, relation contract, one candidate
hidden: generator reasoning, draft answer, other candidates
labels: A=VALID, B=INVALID, C=UNKNOWN
output: calibrated label distribution, margin, entropy, disagreement
```

Direct inspirations:

- Chain-of-Verification: verification questions should be answered
  independently rather than anchored on the draft.
- Calibrate Before Use: label distributions are biased by prompt and label
  format; content-free controls estimate and subtract bias.
- Language Models Hallucinate, but May Excel at Fact Verification: generator
  quality and verifier quality are not the same capability.
- Large Language Models are not Fair Evaluators: LLM judges have positional and
  formatting biases, motivating fixed labels and template-order diagnostics.
- ProCo/key-condition verification: targeted condition reconstruction can be
  more useful than generic "revise your answer" self-correction.
- Large Language Models Cannot Self-Correct Reasoning Yet: supports COVER-KBC's
  design rule that generic self-correction prompts should not be trusted as a
  repair mechanism.

What COVER-KBC changes:

- Verification is candidate-level, not free-form answer rewriting.
- It emits small label distributions, not unbounded judge prose.
- The verifier never sees generator reasoning.
- `UNKNOWN` is represented separately from contradiction.
- The same physical Mistral runtime can fill logical generator/verifier roles
  in E3; role reuse does not imply a second model.

Safe distinction:

```text
COVER-KBC uses verification as typed evidence in a graph, rather than as a
generic self-revision step or a scalar judge score.
```

## 9. Coverage, Completeness, and Uncertainty Literature

This is one of COVER-KBC's strongest conceptual distinctions.

The system separates:

```text
candidate confidence:
  "Is this candidate likely correct?"

answer-space coverage / residual search need:
  "Have we searched enough, or is useful evidence probably still missing?"
```

The Technical Proposal originally considered but rejected literal
capture-recapture/Chao-style cardinality estimation for the core. Audit 0009
confirms that no Chao/cardinality estimator is active. RCSE estimates
need-to-continue, not a hidden-object probability.

Best literature lineage:

- The KB completeness survey by Razniewski et al. is the most relevant broad
  conceptual citation: KB quality requires reasoning about completeness/recall
  and negative knowledge, not only correctness of known facts.
- Adaptive-Consistency and FLARE are relevant because they use ongoing
  agreement/low-confidence signals to decide whether more inference/retrieval
  is worth it.
- SelfCheckGPT and semantic uncertainty are related to disagreement-based
  uncertainty, but they target hallucination/confabulation detection, not
  relation-level answer-set coverage. They are optional citations, not core.

What COVER-KBC changes:

- It translates KB completeness concerns into per-query test-time residual
  search signals.
- It keeps candidate confidence and residual coverage as separate variables.
- It uses relation-specific residual terms: set stability for small sets,
  yield/facet gap for large open sets, numeric dispersion for numeric relations,
  and gate/locality competition for null-single relations.
- It does not claim formal completeness or estimate true unseen cardinality.

Safe distinction:

```text
COVER-KBC operationalizes a lightweight residual search-need signal for
closed-book KBC, separating whether current candidates are credible from
whether the answer set is likely exhausted.
```

## 10. Adaptive Test-Time Inference and Stopping Literature

COVER-KBC's controller is a deterministic test-time policy:

```text
state S_t = evidence graph + candidate states + residual coverage + budget
actions A_t = views, facets, verifier calls, reverse checks, resamples, STOP
policy pi = legality filter + action utility + relation stopping policy
termination = budget exhausted or relation-specific stop condition
```

Direct and useful precedents:

- Adaptive-Consistency is the closest direct citation for non-uniform sample
  allocation and lightweight stopping. It starts from self-consistency and
  stops when additional samples are unlikely to change the result.
- FLARE is relevant for active triggering, but it triggers external retrieval
  from low-confidence generation; COVER triggers closed-book elicitation or
  verification actions.
- Self-RAG is relevant for on-demand retrieve/generate/critique control, but it
  trains reflection tokens and retrieves external passages. COVER-KBC is
  training-free and closed-book.
- ReAct/LATS/ToT are broader action/planning/search precedents. They are useful
  if the paper uses "agentic" formalization, but not necessary in a compact
  Related Work section unless space remains.
- ICLR 2025 test-time compute papers by Snell et al. and Wu et al. provide
  modern framing for compute-optimal inference: spend inference compute
  adaptively rather than only scale model parameters.

What COVER-KBC changes:

- The action space is relation-typed.
- The utility is over verified gain, residual reduction, uncertainty reduction,
  cost, redundancy, and false-positive risk.
- Stopping differs by program type: SMALL_SET, NULL_SINGLE, NUMERIC,
  LARGE_OPEN_SET.
- In E3, some final gains come from fixed Multi-View specialists rather than
  from the full adaptive controller, so claims should distinguish architecture
  from measured winning deltas.

Safe distinction:

```text
COVER-KBC adapts adaptive test-time compute to closed-book KBC by selecting
among relation-specific evidence-acquisition and verification actions under a
fixed model budget.
```

## 11. Multi-View and Numeric Consensus Literature

Profile E3's Area and Capacity specialists are downstream specializations:

```text
four deterministic semantic views
  -> strict parser
  -> 5 percent relative-distance clustering
  -> accept cluster with support >= 3
  -> one source-blind judge if ambiguous
  -> fallback: judge -> V1 -> top cluster -> []
```

Lineage:

- Self-Consistency supplies the general idea of sampling multiple paths and
  aggregating agreement.
- ReWiSe supplies a directly benchmark-specific precedent for relation-wise
  aggregation and numeric majority/median/average choices.
- Atomic Self-Consistency supplies the "merge evidence across outputs rather
  than choose one answer" principle.
- Pairwise Ranking Prompting is a useful background citation for reducing LLM
  burden by comparing a small candidate set rather than requiring full listwise
  judgment. COVER's source-blind judge is not a ranker, but the burden-reduction
  rationale is similar.

What COVER-KBC changes:

- Views are semantically diversified, not just repeated stochastic samples.
- Numeric values are parsed with strict relation-specific grammars.
- Agreement is tolerance-aware:

```text
d(a,b) = |a - b| / max(|a|, |b|)
compatible iff d(a,b) <= 0.05
```

- The judge is source-blind and sees anonymized numeric candidates, not view
  names or support counts.
- Upstream Area/Capacity answers do not become extra votes in E3.

Safe wording:

```text
The numeric final layer is an empirical specialization of established
multi-sample consensus ideas, adapted to ambiguous numeric attributes through
semantic views, strict parsing, and tolerance-aware clustering.
```

Do not overstate novelty of Multi-View itself.

## 12. LM-KBC-Specific Prior Work

LM-KBC papers remain necessary, but they should not dominate the final Related
Work.

Most useful LM-KBC papers:

| Paper | Why cite | Relation to COVER-KBC |
| --- | --- | --- |
| LM-KBC 2025 overview, Kalo et al. | Benchmark context, same six-relation family, closed-book rules | establishes task setting |
| ReWiSe, Albert-Roulhac and Zouaq | relation-wise self-consistency in LM-KBC | closest fixed-sample relation-wise aggregation precedent |
| Self-RAG + Divide-and-Conquer, He and Razniewski | relation-dependent hybrid strategy, award DaC | closest benchmark-level relation-routing system |
| Navigating Nulls, Numbers and Numerous Entities, Das et al. 2024 | null/numeric/many-object focus | useful for task-specific failure modes, optional if space |
| Prompting as Probing, Alivanistos et al. 2022 | early LM-KBC prompting/probing | optional historical task context |

Do not make the final Related Work read like a chronology of LM-KBC systems.
Use only 2-4 LM-KBC citations in final prose.

## 13. Direct Inspirations vs Post-Hoc Related Work

### DIRECT DESIGN INSPIRATION

These are explicitly in the Technical Proposal and shaped implemented modules:

- Chain-of-Verification -> M4/M17 blind verification.
- Calibrate Before Use -> contextual calibration in M4.
- Self-Consistency and Atomic Self-Consistency -> M16 consensus and E3 numeric consensus.
- Adaptive-Consistency -> adaptive sample/control/stopping idea.
- APE/MIPRO/AMPO/BPO/OPRO -> M10 prompt-program idea and Prompt Lab, though not active optimizer.
- Step-Back Prompting and Self-Ask -> M10/M11 semantic decomposition and E3 step-back view.
- FLARE/Self-RAG/HyDE/Query2Doc/Rewrite-Retrieve-Read -> M11 closed-book parametric retrieval design, with external retrieval removed.

### IMPORTANT CONCEPTUAL PRECEDENT

- Knowledge Vault and truth discovery -> provenance-aware fact fusion.
- KB completeness survey -> coverage/missingness distinct from correctness.
- ReAct/ToT/LATS and test-time compute papers -> action/control framing.
- LAMA -> parametric factual knowledge probing.

### BENCHMARK / TASK CONTEXT

- LM-KBC overview papers.
- ReWiSe.
- Self-RAG + DaC.
- Navigating Nulls, Numbers and Numerous Entities.

### POST-HOC RELATED SYSTEMS

These resemble aspects of COVER-KBC but are not clearly what built the system:

- ReWiSe: fixed relation-wise self-consistency.
- Self-RAG + DaC LM-KBC: relation-dependent decomposition.
- Judge, Generator, Executioner: generate-then-judge LM-KBC.
- SelfCheckGPT: black-box consistency for hallucination detection.
- Semantic entropy: semantic clustering of generations for uncertainty.

### NO LONGER RELEVANT OR NOT FINAL-ACTIVE

- DoLa: optional proposal branch, never active.
- Qwen model cards: historical/bakeoff context only; Qwen inactive in E3.
- OPRO/MIPRO/AMPO/BPO as executable optimizers: prompt optimization inspired
  the design, but no such optimizer is active in E3.

## 14. Novelty Decomposition

| COVER-KBC component | Novelty label | Reason |
| --- | --- | --- |
| Relation contracts | SYSTEM-SPECIFIC OPERATIONALIZATION | Schema-aware/relation-aware ideas exist, but COVER makes relation contracts executable across views, verifier, stopping, and selector. |
| Prompt Program Compiler | ADAPTED COMPONENT | Prompt programs and prompt optimization are known; COVER freezes structured prompt blueprints without active optimizer. |
| Closed-book parametric retrieval | ADAPTED COMPONENT | RAG query expansion and self-ask exist; COVER removes external retrieval and treats pseudo-memory as unverified candidate source. |
| Evidence graph | NEW COMBINATION | Evidence/provenance/fusion ideas exist; COVER combines them with LLM prompt-view provenance and relation contracts. |
| Independence groups | ADAPTED COMPONENT | Source dependence/truth discovery motivates it; COVER adapts dependence to prompt mechanisms and model roles. |
| Blind label verifier | ADAPTED COMPONENT | CoVe/calibration/verifier literature exist; COVER turns it into candidate-level graph evidence. |
| Candidate scoring | SYSTEM-SPECIFIC OPERATIONALIZATION | Weighted evidence scores are not novel; the separation of F/L/X/C/U by mechanism is specific and auditable. |
| RCSE residual coverage | POTENTIALLY NOVEL MECHANISM | Related to KB completeness and adaptive stopping, but COVER's relation-typed residual search-need signal is a new combination. Avoid claiming formal completeness. |
| Adaptive controller | NEW COMBINATION | Adaptive inference and agentic loops exist; COVER applies them to relation-typed closed-book KBC action choices. |
| Relation-aware selectors | ADAPTED COMPONENT | Relation-specific aggregation exists in LM-KBC; COVER binds it to typed contracts and evidence state. |
| Numeric Multi-View | ADAPTED COMPONENT | Consensus is known; E3 adaptation to semantic numeric views and strict tolerance clustering is system-specific. |
| Mutation-bounded repair | SYSTEM-SPECIFIC OPERATIONALIZATION | Conservative gated correction is a known engineering principle; COVER applies it per relation to avoid regressions. |

Strongest methodological contributions:

1. Relation-typed active evidence acquisition: a relation selects an executable
   program, not just a prompt.
2. Provenance-aware, independence-aware evidence state with blind calibrated
   verification.
3. Residual-coverage and budget-aware control: candidate confidence is
   separated from answer-space search need.

Safe novelty wording:

```text
COVER-KBC combines relation-typed inference programs, provenance-aware atomic
evidence, blind calibrated verification, and residual-coverage-guided
test-time control for closed-book knowledge-base construction.
```

Dangerous novelty wording:

```text
first relation-aware KBC system
first evidence graph for LLM reasoning
first adaptive LLM KBC system
first verifier-based LM-KBC system
first Multi-View numeric elicitation method
```

## 15. Strongest Papers for Final Citation

### LM-KBC papers

1. `kalo2025lmkbc`
   - Exact title: LM-KBC 2025: 4th Challenge on Knowledge Base Construction from Pre-trained Language Models
   - Authors: Jan-Christoph Kalo, Simon Razniewski, Bohui Zhang, Tuan-Phong Nguyen
   - Year/venue: 2025, CEUR-WS Vol. 4041, Joint KBC-LM/LM-KBC at ISWC
   - Why it belongs: benchmark and task context
   - Component supported: closed-book LM-KBC setting
   - Type: BENCHMARK CONTEXT
   - Priority: MUST CITE

2. `albertRoulhac2025rewise`
   - Exact title: ReWiSe: Relation-Wise Self-consistency for LLM Probing
   - Authors: Edouard Albert-Roulhac, Amal Zouaq
   - Year/venue: 2025, CEUR-WS Vol. 4041
   - Why it belongs: strongest LM-KBC self-consistency baseline
   - Component supported: relation-wise aggregation and numeric consensus contrast
   - Type: POST-HOC RELATED WORK
   - Priority: SHOULD CITE

3. `he2025selfragdac`
   - Exact title: Combining Self-Retrieval-Augmented Generation with Divide-and-Conquer for Language Model-based Knowledge Base Construction
   - Authors: Jingbo He, Simon Razniewski
   - Year/venue: 2025, CEUR-WS Vol. 4041
   - Why it belongs: closest relation-dependent benchmark system
   - Component supported: relation-specific strategy and award decomposition contrast
   - Type: POST-HOC RELATED WORK
   - Priority: SHOULD CITE

### Broader methodological papers

4. `petroni2019language`
   - Exact title: Language Models as Knowledge Bases?
   - Authors: Fabio Petroni et al.
   - Year/venue: 2019, EMNLP-IJCNLP
   - Why it belongs: foundational parametric factual knowledge probing
   - Component supported: closed-book parametric elicitation
   - Type: CONCEPTUAL PRECEDENT
   - Priority: SHOULD CITE

5. `opsahlOng2024optimizing`
   - Exact title: Optimizing Instructions and Demonstrations for Multi-Stage Language Model Programs
   - Authors: Krista Opsahl-Ong et al.
   - Year/venue: 2024, EMNLP
   - Why it belongs: best broad citation for modular LM programs
   - Component supported: relation-typed prompt/action programs
   - Type: CONCEPTUAL PRECEDENT / DIRECT DESIGN INSPIRATION for M10
   - Priority: SHOULD CITE if prompt-program framing appears

6. `wang2023selfconsistency`
   - Exact title: Self-Consistency Improves Chain of Thought Reasoning in Language Models
   - Authors: Xuezhi Wang et al.
   - Year/venue: 2023, ICLR
   - Why it belongs: foundational multi-sample consensus
   - Component supported: multi-view elicitation and numeric consensus
   - Type: DIRECT DESIGN INSPIRATION
   - Priority: MUST CITE

7. `thirukovalluru2024atomic`
   - Exact title: Atomic Self-Consistency for Better Long Form Generations
   - Authors: Raghuveer Thirukovalluru, Yukun Huang, Bhuwan Dhingra
   - Year/venue: 2024, EMNLP
   - Why it belongs: atomic evidence merging rather than one-answer selection
   - Component supported: M16 atomic consensus/evidence graph
   - Type: DIRECT DESIGN INSPIRATION
   - Priority: MUST/SHOULD CITE

8. `dhuliawala2024chain`
   - Exact title: Chain-of-Verification Reduces Hallucination in Large Language Models
   - Authors: Shehzaad Dhuliawala et al.
   - Year/venue: 2024, Findings ACL
   - Why it belongs: independent verification questions and blind verification principle
   - Component supported: M4/M17 verifier
   - Type: DIRECT DESIGN INSPIRATION
   - Priority: MUST CITE

9. `zhao2021calibrate`
   - Exact title: Calibrate Before Use: Improving Few-shot Performance of Language Models
   - Authors: Zihao Zhao, Eric Wallace, Shi Feng, Dan Klein, Sameer Singh
   - Year/venue: 2021, ICML
   - Why it belongs: content-free contextual calibration
   - Component supported: calibrated verifier labels
   - Type: DIRECT DESIGN INSPIRATION
   - Priority: MUST CITE

10. `press2023measuring`
    - Exact title: Measuring and Narrowing the Compositionality Gap in Language Models
    - Authors: Ofir Press et al.
    - Year/venue: 2023, Findings EMNLP
    - Why it belongs: Self-Ask decomposition and structured follow-up questions
    - Component supported: M11 self-ask and decomposition
    - Type: DIRECT DESIGN INSPIRATION
    - Priority: OPTIONAL/SHOULD depending on space

11. `jiang2023active`
    - Exact title: Active Retrieval Augmented Generation
    - Authors: Zhengbao Jiang et al.
    - Year/venue: 2023, EMNLP
    - Why it belongs: active trigger for retrieving more information
    - Component supported: adaptive ask-more control, with no external retrieval in COVER
    - Type: DIRECT CONCEPTUAL INSPIRATION
    - Priority: SHOULD CITE

12. `aggarwal2023lets`
    - Exact title: Let's Sample Step by Step: Adaptive-Consistency for Efficient Reasoning and Coding with LLMs
    - Authors: Pranjal Aggarwal, Aman Madaan, Yiming Yang, Mausam
    - Year/venue: 2023, EMNLP
    - Why it belongs: closest adaptive self-consistency/stopping precedent
    - Component supported: M7/M20/M21 adaptive compute
    - Type: DIRECT DESIGN INSPIRATION
    - Priority: MUST/SHOULD CITE

13. `razniewski2024completeness`
    - Exact title: Completeness, Recall, and Negation in Open-world Knowledge Bases: A Survey
    - Authors: Simon Razniewski, Hiba Arnaout, Shrestha Ghosh, Fabian Suchanek
    - Year/venue: 2024, ACM Computing Surveys
    - Why it belongs: best conceptual citation for completeness/recall/missingness
    - Component supported: RCSE and M19 residual coverage distinction
    - Type: IMPORTANT CONCEPTUAL PRECEDENT
    - Priority: SHOULD CITE

14. `dong2014knowledge`
    - Exact title: Knowledge Vault: A Web-Scale Approach to Probabilistic Knowledge Fusion
    - Authors: Xin Luna Dong et al.
    - Year/venue: 2014, KDD
    - Why it belongs: classic probabilistic fact fusion and source evidence
    - Component supported: provenance-aware evidence aggregation
    - Type: CONCEPTUAL PRECEDENT
    - Priority: SHOULD/OPTIONAL depending on space

This 14-paper shortlist has 3 LM-KBC papers and 11 broader methodological
papers. That is the right balance for the final paper if Related Work is only
250-350 words.

## 16. Recommended 2-3 Subsection Structure

Recommended final structure: **three compact subsections**.

### 2.1 Closed-Book Knowledge Construction

Main argument:

Pretrained LMs can be probed for factual relational knowledge, and LM-KBC turns
that into a closed-book KB construction benchmark. Prior LM-KBC systems use
relation-wise self-consistency and decomposition, but COVER-KBC expands the
idea into executable relation-typed inference programs.

Papers:

- Petroni et al. 2019
- Kalo et al. 2025
- ReWiSe 2025
- Self-RAG+DaC 2025

Target length: 80-100 words.

Transition:

```text
However, relation-specific prompting alone does not explain how COVER-KBC
represents and adjudicates competing evidence.
```

### 2.2 Evidence, Verification, and Calibration

Main argument:

Self-consistency and atomic self-consistency show that multiple generations can
be aggregated, while CoVe and calibration work motivate independent, bias-aware
verification. COVER-KBC combines these into provenance-aware candidate evidence
with blind calibrated verification.

Papers:

- Self-Consistency 2023
- Atomic Self-Consistency 2024
- Chain-of-Verification 2024
- Calibrate Before Use 2021
- Knowledge Vault 2014 or truth discovery survey if space allows

Target length: 100-120 words.

Transition:

```text
The remaining question is how much evidence to acquire for each relation.
```

### 2.3 Adaptive Relation-Typed Test-Time Control

Main argument:

Adaptive retrieval, adaptive consistency, and test-time compute scaling show
that inference compute should be allocated non-uniformly. KB completeness
literature motivates separating correctness from answer-set coverage. COVER-KBC
adapts these ideas to closed-book KBC via residual coverage and
relation-specific action/stopping policies.

Papers:

- Adaptive-Consistency 2023
- FLARE 2023 or Self-RAG 2024
- Snell et al. 2025 or Wu et al. 2025
- Razniewski et al. 2024 completeness survey

Target length: 90-120 words.

## 17. 250-350 Word Related-Work Content Plan

Do not paste this directly into the paper; it is a compact plan for final prose.

Paragraph 1, closed-book KBC:

- Start with LMs as repositories of relational knowledge, citing LAMA.
- Introduce LM-KBC as the shared-task setting and cite the 2025 overview.
- Mention ReWiSe and Self-RAG+DaC as task-specific systems that already use
  relation-wise self-consistency/decomposition.
- Contrast: COVER-KBC treats the relation as an executable contract that
  controls views, parsing, verification, stopping, and selection.

Paragraph 2, evidence and verification:

- Cite Self-Consistency and Atomic Self-Consistency for multi-generation
  aggregation and atomic merging.
- Cite CoVe for independent verification and Zhao et al. for calibration.
- Optionally cite Knowledge Vault for evidence/provenance/fusion.
- Contrast: COVER-KBC stores candidate-level, provenance-aware evidence and
  uses a blind A/B/C verifier rather than free-form judge prose.

Paragraph 3, adaptive control:

- Cite Adaptive-Consistency and FLARE/Self-RAG for non-uniform test-time
  information acquisition.
- Cite test-time compute scaling if there is room.
- Cite KB completeness survey for separating correctness from coverage.
- Contrast: COVER-KBC applies this to closed-book KBC by estimating residual
  search need and using relation-specific action/stopping policies.

Expected final citation density: 10-14 citations total. Avoid long prose on any
single prior LM-KBC submission.

## 18. Claims We Can Safely Make

- COVER-KBC is closed-book and uses no web/RAG/external KB at inference.
- COVER-KBC uses relation contracts that control more than prompt wording.
- COVER-KBC maintains a provenance-aware evidence graph rather than a flat
  list of strings.
- COVER-KBC uses blind candidate verification with calibrated A/B/C label
  distributions.
- COVER-KBC separates candidate confidence from residual answer-space search
  need.
- COVER-KBC includes adaptive, budget-aware action selection in the core/V3
  architecture.
- Profile E3's final improvements come from conservative relation-specific
  specializations, especially numeric Multi-View for Area/Capacity.
- The strongest novelty is a system-level combination/operationalization, not
  invention of every component.

## 19. Claims We Must Avoid

- Do not claim COVER-KBC invented self-consistency, prompt ensembling,
  verification, calibration, RAG-style query rewriting, or test-time compute.
- Do not claim COVER-KBC is the first relation-aware LM-KBC system.
- Do not claim COVER-KBC is the first evidence graph for LLM reasoning.
- Do not claim RCSE estimates true hidden answer cardinality.
- Do not claim residual coverage is a calibrated probability of missing gold
  objects.
- Do not claim M9-M21 shadow modules all drove the hidden E3 score.
- Do not describe Qwen, DoLa, CHIV, C2, NSMV, or active external retrieval as
  part of Profile E3.
- Do not present numeric Multi-View as a wholly novel method; present it as an
  effective relation-specific adaptation of consensus.
- Do not say hidden TEST is independent validation after repeated iterative
  leaderboard probes; use careful "user-provided leaderboard evidence" wording.

## 20. Verified BibTeX Candidates

Status notes:

- `VERIFIED` means metadata was verified from ACL Anthology, CEUR, PMLR, ACM,
  Google Research, NeurIPS/ICLR/ICML proceedings pages, or DBLP/proceedings
  metadata.
- `NEEDS MANUAL CHECK` means the bibliographic entry is likely correct but the
  canonical OpenReview BibTeX should be checked before final camera-ready.

### VERIFIED: LM-KBC 2025 overview

```bibtex
@inproceedings{kalo2025lmkbc,
  title = {{LM-KBC} 2025: 4th Challenge on Knowledge Base Construction from Pre-trained Language Models},
  author = {Kalo, Jan-Christoph and Razniewski, Simon and Zhang, Bohui and Nguyen, Tuan-Phong},
  booktitle = {Joint Proceedings of the Workshops on Knowledge Base Construction from Pre-Trained Language Models and Knowledge Base Construction, Reasoning and Mining},
  series = {{CEUR} Workshop Proceedings},
  volume = {4041},
  year = {2025},
  url = {https://ceur-ws.org/Vol-4041/paper7.pdf}
}
```

### VERIFIED: ReWiSe

```bibtex
@inproceedings{albertRoulhac2025rewise,
  title = {{ReWiSe}: Relation-Wise Self-consistency for {LLM} Probing},
  author = {Albert-Roulhac, Edouard and Zouaq, Amal},
  booktitle = {Joint Proceedings of the Workshops on Knowledge Base Construction from Pre-Trained Language Models and Knowledge Base Construction, Reasoning and Mining},
  series = {{CEUR} Workshop Proceedings},
  volume = {4041},
  year = {2025},
  url = {https://ceur-ws.org/Vol-4041/paper8.pdf}
}
```

### VERIFIED: Self-RAG + Divide-and-Conquer LM-KBC

```bibtex
@inproceedings{he2025selfragdac,
  title = {Combining Self-Retrieval-Augmented Generation with Divide-and-Conquer for Language Model-based Knowledge Base Construction},
  author = {He, Jingbo and Razniewski, Simon},
  booktitle = {Joint Proceedings of the Workshops on Knowledge Base Construction from Pre-Trained Language Models and Knowledge Base Construction, Reasoning and Mining},
  series = {{CEUR} Workshop Proceedings},
  volume = {4041},
  year = {2025},
  url = {https://ceur-ws.org/Vol-4041/paper11.pdf}
}
```

### VERIFIED: LAMA

```bibtex
@inproceedings{petroni2019language,
  title = "Language Models as Knowledge Bases?",
  author = {Petroni, Fabio and Rockt{\"a}schel, Tim and Riedel, Sebastian and Lewis, Patrick and Bakhtin, Anton and Wu, Yuxiang and Miller, Alexander},
  booktitle = {Proceedings of the 2019 Conference on Empirical Methods in Natural Language Processing and the 9th International Joint Conference on Natural Language Processing},
  pages = {2463--2473},
  year = {2019},
  publisher = {Association for Computational Linguistics},
  doi = {10.18653/v1/D19-1250},
  url = {https://aclanthology.org/D19-1250/}
}
```

### VERIFIED: MIPRO

```bibtex
@inproceedings{opsahlOng2024optimizing,
  title = {Optimizing Instructions and Demonstrations for Multi-Stage Language Model Programs},
  author = {Opsahl-Ong, Krista and Ryan, Michael J and Purtell, Josh and Broman, David and Potts, Christopher and Zaharia, Matei and Khattab, Omar},
  booktitle = {Proceedings of the 2024 Conference on Empirical Methods in Natural Language Processing},
  pages = {9340--9366},
  year = {2024},
  publisher = {Association for Computational Linguistics},
  doi = {10.18653/v1/2024.emnlp-main.525},
  url = {https://aclanthology.org/2024.emnlp-main.525/}
}
```

### NEEDS MANUAL CHECK: Self-Consistency

```bibtex
@inproceedings{wang2023selfconsistency,
  title = {Self-Consistency Improves Chain of Thought Reasoning in Language Models},
  author = {Wang, Xuezhi and Wei, Jason and Schuurmans, Dale and Le, Quoc V. and Chi, Ed H. and Narang, Sharan and Chowdhery, Aakanksha and Zhou, Denny},
  booktitle = {International Conference on Learning Representations},
  year = {2023},
  url = {https://mlanthology.org/iclr/2023/wang2023iclr-selfconsistency/}
}
```

### VERIFIED: Atomic Self-Consistency

```bibtex
@inproceedings{thirukovalluru2024atomic,
  title = {Atomic Self-Consistency for Better Long Form Generations},
  author = {Thirukovalluru, Raghuveer and Huang, Yukun and Dhingra, Bhuwan},
  booktitle = {Proceedings of the 2024 Conference on Empirical Methods in Natural Language Processing},
  pages = {12681--12694},
  year = {2024},
  publisher = {Association for Computational Linguistics},
  doi = {10.18653/v1/2024.emnlp-main.706},
  url = {https://aclanthology.org/2024.emnlp-main.706/}
}
```

### VERIFIED: Chain-of-Verification

```bibtex
@inproceedings{dhuliawala2024chain,
  title = {Chain-of-Verification Reduces Hallucination in Large Language Models},
  author = {Dhuliawala, Shehzaad and Komeili, Mojtaba and Xu, Jing and Raileanu, Roberta and Li, Xian and Celikyilmaz, Asli and Weston, Jason},
  booktitle = {Findings of the Association for Computational Linguistics: ACL 2024},
  pages = {3563--3578},
  year = {2024},
  publisher = {Association for Computational Linguistics},
  doi = {10.18653/v1/2024.findings-acl.212},
  url = {https://aclanthology.org/2024.findings-acl.212/}
}
```

### VERIFIED: Calibrate Before Use

```bibtex
@inproceedings{zhao2021calibrate,
  title = {Calibrate Before Use: Improving Few-shot Performance of Language Models},
  author = {Zhao, Zihao and Wallace, Eric and Feng, Shi and Klein, Dan and Singh, Sameer},
  booktitle = {Proceedings of the 38th International Conference on Machine Learning},
  series = {Proceedings of Machine Learning Research},
  volume = {139},
  pages = {12697--12706},
  year = {2021},
  publisher = {PMLR},
  url = {https://proceedings.mlr.press/v139/zhao21c.html}
}
```

### VERIFIED: Self-Ask / compositionality gap

```bibtex
@inproceedings{press2023measuring,
  title = {Measuring and Narrowing the Compositionality Gap in Language Models},
  author = {Press, Ofir and Zhang, Muru and Min, Sewon and Schmidt, Ludwig and Smith, Noah and Lewis, Mike},
  booktitle = {Findings of the Association for Computational Linguistics: EMNLP 2023},
  pages = {5687--5711},
  year = {2023},
  publisher = {Association for Computational Linguistics},
  doi = {10.18653/v1/2023.findings-emnlp.378},
  url = {https://aclanthology.org/2023.findings-emnlp.378/}
}
```

### VERIFIED: FLARE

```bibtex
@inproceedings{jiang2023active,
  title = {Active Retrieval Augmented Generation},
  author = {Jiang, Zhengbao and Xu, Frank and Gao, Luyu and Sun, Zhiqing and Liu, Qian and Dwivedi-Yu, Jane and Yang, Yiming and Callan, Jamie and Neubig, Graham},
  booktitle = {Proceedings of the 2023 Conference on Empirical Methods in Natural Language Processing},
  pages = {7969--7992},
  year = {2023},
  publisher = {Association for Computational Linguistics},
  doi = {10.18653/v1/2023.emnlp-main.495},
  url = {https://aclanthology.org/2023.emnlp-main.495/}
}
```

### VERIFIED: Adaptive-Consistency

```bibtex
@inproceedings{aggarwal2023lets,
  title = {Let's Sample Step by Step: Adaptive-Consistency for Efficient Reasoning and Coding with {LLM}s},
  author = {Aggarwal, Pranjal and Madaan, Aman and Yang, Yiming and Mausam},
  booktitle = {Proceedings of the 2023 Conference on Empirical Methods in Natural Language Processing},
  pages = {12375--12396},
  year = {2023},
  publisher = {Association for Computational Linguistics},
  doi = {10.18653/v1/2023.emnlp-main.761},
  url = {https://aclanthology.org/2023.emnlp-main.761/}
}
```

### VERIFIED: KB completeness survey

```bibtex
@article{razniewski2024completeness,
  title = {Completeness, Recall, and Negation in Open-world Knowledge Bases: A Survey},
  author = {Razniewski, Simon and Arnaout, Hiba and Ghosh, Shrestha and Suchanek, Fabian},
  journal = {ACM Computing Surveys},
  volume = {56},
  number = {6},
  articleno = {150},
  year = {2024},
  doi = {10.1145/3639563},
  url = {https://doi.org/10.1145/3639563}
}
```

### VERIFIED: Knowledge Vault

```bibtex
@inproceedings{dong2014knowledge,
  title = {Knowledge Vault: A Web-Scale Approach to Probabilistic Knowledge Fusion},
  author = {Dong, Xin Luna and Gabrilovich, Evgeniy and Heitz, Geremy and Horn, Wilko and Lao, Ni and Murphy, Kevin and Strohmann, Thomas and Sun, Shaohua and Zhang, Wei},
  booktitle = {Proceedings of the 20th ACM SIGKDD International Conference on Knowledge Discovery and Data Mining},
  pages = {601--610},
  year = {2014},
  publisher = {ACM},
  url = {https://research.google/pubs/knowledge-vault-a-web-scale-approach-to-probabilistic-knowledge-fusion/}
}
```

## 21. Missing / Unverified Sources

The following are useful but should be manually checked before final paper
submission if cited:

- Official OpenReview BibTeX for Self-Consistency, ReAct, Step-Back Prompting,
  DoLa, SELF-DISCOVER, Self-RAG, and Snell et al. 2025. Search results and
  proceedings/DBLP metadata were adequate for this dossier, but canonical
  camera-ready BibTeX should be copied from OpenReview or proceedings.
- Exact official 2026 AKBC shared-task page metadata. The proposal cites the
  task page, but this review did not use web search for the 2026 task page in
  order to keep focus on broader method lineage.
- Full bibliographic details for all model-card entries. They are not likely
  final Related Work citations.
- Truth discovery/source-dependence papers beyond Knowledge Vault and the KB
  completeness survey. They are useful for appendix discussion of independence
  groups, but the six-page paper probably has no room for a deep data-fusion
  lineage.

Final recommendation:

```text
Do not frame COVER-KBC as "we use several prompts and vote."
Frame it as a closed-book, relation-typed, evidence-centric KBC system:
relation contracts select evidence-acquisition and verification programs;
candidate observations become provenance-aware evidence; blind calibrated
verification and residual coverage guide relation-specific finalization and
test-time compute.
```

