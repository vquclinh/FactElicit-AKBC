# COVER-KBC Related Work Research Review

Review metadata:

- Repository HEAD: `b94f0089b261ff98027bcdaa7d2dd9027191e10d`
- Review date: 2026-08-13
- Output file created by this task: `docs/PAPER_RELATED_WORK_REVIEW.md`
- Worktree policy: read-only except this new Markdown file.
- Hidden TEST inference: not run.
- Expensive neural inference: not run.

Repository status at the start of this task was already dirty; this task does not analyze those changes as authored by this review. No source, config, test, script, or notebook file was modified.

## 1. Scope and Method

This dossier is not final Related Work prose. It is a research dossier for a later ACL-style paper writer. The goal is to identify the smallest defensible set of prior-work themes for COVER-KBC and to verify candidate references well enough that the final paper does not overclaim novelty.

Repository material inspected for system understanding:

- `README.md`
- `docs/PAPER_SYSTEM_SUMMARY.md`
- `docs/PAPER_FULL_ARCHITECTURE_REVIEW.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/audits/`
- `src/cover_kbc/contracts/`
- `src/cover_kbc/elicitation/`
- `src/cover_kbc/evidence/`
- `src/cover_kbc/verification/`
- `src/cover_kbc/scoring.py`
- `src/cover_kbc/coverage.py`
- `src/cover_kbc/controller.py`
- `src/cover_kbc/selection.py`
- `src/cover_kbc/control/`
- `src/cover_kbc/coverage_gap/`
- `src/cover_kbc/v3_core/`
- `src/cover_kbc/v3_1/`
- `src/cover_kbc/leaderboard_repair/`
- relevant tests for contracts, evidence, verifier, RCSE, controller, selectors, V3, and Profile E1/E2/E3.

Repository files/concepts inspected:

- 421 files across `README.md`, `docs`, `configs`, `src/cover_kbc`, `tests`, `scripts`, and `benchmark` were enumerated.
- The review focused on 31 production-reachable components and the main shadow/post-architecture components catalogued in `docs/PAPER_FULL_ARCHITECTURE_REVIEW.md`.
- No local `.bib` file was found.

External research method:

- Primary sources were preferred: CEUR Workshop Proceedings for LM-KBC systems, ACL Anthology, PMLR, official NeurIPS proceedings, IBM/OpenReview-linked official paper pages, Google Research publication pages, and university/publisher metadata pages.
- Random blogs and secondary summaries were avoided.
- OpenReview pages themselves triggered a browser verification wall during this review. For those papers, metadata was verified from alternative authoritative or semi-authoritative sources such as official project pages, ML Anthology, DBLP, NeurIPS/PMLR, or paper-author pages where available. Entries depending on blocked OpenReview metadata are marked accordingly.

## 2. COVER-KBC Concepts Requiring Related Work

The active system should not be positioned as "four prompts and vote." The repository shows a deeper architecture.

### A. Active core contributions

These are reachable in the current Profile E3 core:

- Relation contracts: `RelationContract` maps each relation to `ProgramType`, output type, cardinality, hard negatives, mandatory/optional views, verification policy, stopping policy, and selection policy.
- Typed elicitation: views are tied to relation semantics and independence groups.
- Provenance-aware evidence graph: candidate support, contradiction, unknown, verifier evidence, prompt records, and model-call provenance are stored explicitly.
- Blind/calibrated verification: candidate-level verifier sees subject, relation definition, and candidate, not generator reasoning; it emits `VALID`, `INVALID`, `UNKNOWN` probabilities.
- Candidate scoring: `S(o) = alpha F + beta L + gamma X - delta C - eta U`, with support, logit evidence, cross-model evidence, contradiction, and disagreement separated.
- Residual coverage estimation: RCSE estimates whether another action is likely to add useful verified information; it is distinct from candidate correctness.
- Adaptive controller: action legality, utility, budget, and relation-specific stopping decide whether to run more views, verify, reverse-check, resample, or stop.
- Relation-aware final selection: SMALL_SET, NULL_SINGLE, NUMERIC, and LARGE_OPEN_SET selectors differ.

### B. Active final specializations

The current hidden-best Profile E3 final layer:

- Award: deterministic metadata normalization and dedupe.
- City: empty-only Mistral city rescue guarded by life/death status and strict city parsing.
- Area: four deterministic semantic views, strict `AREA:` parser, 5 percent numeric clustering, one source-blind judge if needed.
- Capacity: four deterministic semantic views, strict `CAPACITY:` parser, 5 percent numeric clustering, one source-blind judge if needed.
- Stock and Border: no post-pipeline repair in E3.

### C. Shadow/diagnostic ideas

These are paper-relevant but should not be described as experimentally validated final contributors unless ablation evidence is added:

- M9 query intelligence.
- M10 prompt program compiler.
- M11 closed-book parametric retrieval.
- M12-M15 specialists.
- M16 atomic consensus.
- M17/M18 specialist and bidirectional verifiers.
- M19 coverage-gap/novelty estimator.
- M20 relation budget scheduler.
- M21 micro-planner.
- V3 hypothesis/action family graph where production-gated.

### D. Historical/retired ideas

These should appear only as ablation/provenance if needed:

- Qwen verifier portfolio.
- C2 aggressive repairs.
- CHIV/NSMV capacity probes.
- broad numeric repair stacks.
- direct border directional sweep experiments.
- old award witness/time-sliced recall modules.

## 3. Literature Theme Map

The smallest useful set of Related Work themes for a six-page system paper is three subsections:

1. Closed-book knowledge-base construction and LM-KBC.
2. Multi-view elicitation, verification, and evidence aggregation.
3. Adaptive relation-aware inference, coverage, and stopping.

Why not more subsections:

- Numeric elicitation is important empirically, but in the final paper it should be treated as a downstream specialization under multi-view elicitation or relation-aware finalization.
- Prompt engineering, self-consistency, and verification overlap heavily in LM-KBC prior systems.
- Coverage/completeness and adaptive test-time compute belong together for COVER-KBC because RCSE drives action selection and stopping.

Candidate themes and recommendation:

| Theme | Use in final Related Work? | Reason |
| --- | --- | --- |
| LM-KBC shared-task systems | Yes, central | Direct benchmark context |
| Closed-book parametric KBC | Yes, central | Defines challenge setting and distinction from RAG |
| Relation-aware/schema-aware prompting | Yes, central | Contracts are a core contribution |
| Self-consistency/multi-view elicitation | Yes | Contextualizes E3 Area/Capacity and core independent views |
| Verification/self-correction | Yes | Contextualizes blind verifier and candidate judging |
| Evidence aggregation/provenance | Yes, but compact | Relevant to graph and support accounting |
| Adaptive inference/test-time compute | Yes | Contextualizes controller |
| Coverage/completeness estimation | Yes, but compact | Distinguishes candidate confidence from answer completeness |
| Numeric factual elicitation | Maybe one paragraph only | Important but not the whole method |
| General RAG | Mention as contrast only | Current system is closed-book |
| Trainable prompt tuning | Mention as contrast only | COVER-KBC does not train/fine-tune |

## 4. Prior LM-KBC Systems

This section is mandatory for the final paper. The most directly relevant sources are the LM-KBC challenge overview/system-description proceedings.

### LM-KBC 2025 overview

Verified source:

- Jan-Christoph Kalo, Simon Razniewski, Bohui Zhang, Tuan-Phong Nguyen. "LM-KBC 2025: 4th Challenge on Knowledge Base Construction from Pre-trained Language Models." CEUR Vol. 4041, KBC-LM+LM-KBC 2025, co-located with ISWC 2025. URL: https://ceur-ws.org/Vol-4041/paper7.pdf

Relevant verified claims:

- The task is to generate all correct object entities for a subject-relation pair using LMs.
- External resources such as web search and RAG were prohibited in the 2025 setting.
- The six relations are exactly the same family now used by the local benchmark snapshot: `awardWonBy`, `companyTradesAtStockExchange`, `countryLandBordersCountry`, `hasArea`, `hasCapacity`, `personHasCityOfDeath`.
- The 2025 challenge standardized on Qwen3-8B and received five submissions.
- The overview identifies self-consistency, self-RAG, reasoning, and prompt optimization as major submitted ideas.

Methodological comparison:

- COVER-KBC continues the closed-book, multi-cardinality KBC framing but uses one Mistral-Small-3.2-24B checkpoint under the 2026 32B budget rather than the 2025 fixed Qwen3-8B setting.
- COVER-KBC is more stateful than the overview baseline: it has relation contracts, evidence graph, verifier, residual coverage, adaptive controller, and relation-aware selectors.

### ReWiSe: Relation-Wise Self-consistency for LLM Probing

Verified source:

- Edouard Albert-Roulhac, Amal Zouaq. "ReWiSe: Relation-Wise Self-consistency for LLM Probing." CEUR Vol. 4041, 2025. URL: https://ceur-ws.org/Vol-4041/paper8.pdf

Method summary:

- Uses chain-of-thought reasoning and relation-wise self-consistency.
- Builds synthetic chain-of-thought examples from manually written reasoning paths.
- Samples multiple independent answers, with self-consistency parameter `n_c = 20` in the reported main test setting.
- Aggregates at entity level with strategies chosen relation-wise on validation data.
- Numerical relations can use majority, median, or average aggregation, chosen per relation.
- Closed-book/no external corpus in the LM-KBC 2025 setting.
- Uses Qwen3-8B per challenge constraints.

Similarity to COVER-KBC:

- Both use relation-aware aggregation and exploit multiple model calls.
- Both treat numeric and set-valued relations differently.
- Both are closed-book LM-KBC systems.

Difference:

- ReWiSe is primarily fixed-sample self-consistency plus relation-wise aggregation; COVER-KBC maintains an evidence graph with provenance, blind verification, candidate scoring, residual coverage, adaptive action selection, and relation-specific selectors.
- ReWiSe uses synthetic CoT few-shot examples; COVER-KBC current Profile E3 does not train or construct a CoT dataset.

Safe positioning sentence:

> ReWiSe shows the value of relation-wise self-consistency for LM-KBC, whereas COVER-KBC generalizes relation adaptation into executable contracts, provenance-aware evidence state, verification, adaptive control, and relation-specific finalization.

### Judge, Generator, Executioner

Verified source:

- Alex Clay, Ernesto Jimenez-Ruiz, Pranava Madhyastha. "Judge, Generator, Executioner: Utilizing an LLM for KBC." CEUR Vol. 4041, 2025. URL: https://ceur-ws.org/Vol-4041/paper9.pdf

Method summary:

- Uses the same LLM as generator and judge.
- Avoids additional tools or information.
- Generates candidate triples, reruns low-quality candidates, applies consensus, and applies a final multi-pass quality judge.
- The judge scores candidate triples on a 0-100 scale.
- Uses Qwen3-8B without fine-tuning or RAG.

Similarity to COVER-KBC:

- Both generate candidates and then judge/verify them.
- Both remain closed-book.
- Both recognize that candidate filtering is central.

Difference:

- Judge, Generator, Executioner uses free-form numeric scoring by the LLM and iterative reruns. COVER-KBC uses a deliberately blind label-scored verifier with `VALID`, `INVALID`, `UNKNOWN` probabilities and explicit graph evidence.
- COVER-KBC separates support, contradiction, verifier log-odds, and prompt disagreement; it does not rely on one judge score as the main representation.

Safe positioning sentence:

> Prior LM-KBC judge systems used the LLM to score generated triples, while COVER-KBC turns verification into typed candidate evidence with calibrated label probabilities and keeps it separate from acquisition evidence.

### Examining 4-bit quantized Qwen3-8B for object prediction

Verified source:

- Irene Mary Sam. "Examining 4-bit quantized Qwen3-8B for object prediction @ LM-KBC 2025." CEUR Vol. 4041, 2025. URL: https://ceur-ws.org/Vol-4041/paper10.pdf

Method summary:

- Uses 4-bit quantized Qwen3-8B.
- Uses three system prompts and six relation-specific user prompts.
- Treats `awardWonBy` specially and uses different system prompts for multi-object relations.
- Removes thinking-mode text but otherwise uses no additional post-processing in the main system.
- Explores but does not retain twice-prompted correction and 8-bit quantization variants.

Similarity:

- Relation-specific prompts and quantized local runtime are relevant.

Difference:

- COVER-KBC uses relation contracts, evidence graph, verifier, controller, and final repair stack; Sam's main system is much closer to direct relation-specific prompting.
- COVER-KBC uses Mistral-Small-3.2-24B in E3, not Qwen3-8B.

Safe positioning sentence:

> Relation-specific prompt templates were already explored in LM-KBC 2025, but COVER-KBC makes relation semantics executable through contracts that also determine parsing, evidence accounting, verification, stopping, and selection.

### Combining Self-RAG with Divide-and-Conquer

Verified source:

- Jingbo He, Simon Razniewski. "Combining Self-Retrieval-Augmented Generation with Divide-and-Conquer for Language Model-based Knowledge Base Construction." CEUR Vol. 4041, 2025. URL: https://ceur-ws.org/Vol-4041/paper11.pdf

Method summary:

- A hybrid LM-only system for LM-KBC 2025.
- Uses Self-RAG-inspired internal entity descriptions rather than external retrieval.
- Applies a divide-and-conquer module to `awardWonBy`.
- Uses strict output specifications and name validation.
- Reports that divide-and-conquer is useful for high-cardinality awards but harmful/costly for medium-cardinality relations.

Similarity:

- Very close system-level motivation: relation characteristics determine strategy.
- It explicitly supports the claim that no single extraction strategy dominates all relation types.
- It uses internal descriptions as retrieval substitutes, similar in spirit to COVER-KBC M11 parametric recall and description/extraction views.

Difference:

- The He/Razniewski system has a two-path relation router: award vs other. COVER-KBC has a richer relation contract layer and typed program families.
- COVER-KBC uses graph evidence, blind verification, candidate scoring, residual coverage, and an adaptive controller rather than a fixed two-pipeline architecture.
- COVER-KBC's final E3 award method is not divide-and-conquer; it uses core award enumeration plus deterministic metadata normalization.

Safe positioning sentence:

> He and Razniewski demonstrate that high-cardinality and smaller-cardinality LM-KBC relations benefit from different extraction strategies; COVER-KBC pushes this idea into a general contract-driven evidence and control architecture.

### Soft Thinking

Verified source:

- Aldan Creo, Christophe Gueret, Alberto Bernardi, Adrianna Janik, Luca Costabello. "Soft Thinking: Enhancing Knowledge Base Completion through Trainable Prompts in the Chain of Thought." CEUR Vol. 4041, 2025. URL: https://ceur-ws.org/Vol-4041/paper12.pdf

Method summary:

- Inserts relation-specific trainable soft prompts into the chain-of-thought region.
- Keeps base Qwen3-8B weights frozen but trains relation-specific embedding parameters.
- Reports third place in LM-KBC 2025.

Similarity:

- Relation-specific behavior is central.

Difference:

- COVER-KBC performs no training, no prompt tuning, no LoRA, and no trainable extra-model parameters.
- COVER-KBC relation adaptation is explicit and auditable in contracts, evidence, and selectors rather than learned soft embeddings.

Safe positioning sentence:

> Soft Thinking learns relation-specific prompting parameters, whereas COVER-KBC keeps inference training-free and represents relation specialization as explicit contracts, policies, and deterministic finalizers.

### LM-KBC 2024 overview

Verified source:

- Jan-Christoph Kalo, Tuan-Phong Nguyen, Simon Razniewski, Bohui Zhang. "Preface: LM-KBC Challenge 2024." CEUR Vol. 3853, 2024. URL: https://ceur-ws.org/Vol-3853/paper0.pdf

Relevant claims:

- LM-KBC differs from LAMA-style probing by requiring actual KB construction, no simplifying cardinality assumptions, and materialized accept/reject decisions.
- 2024 used a 10B parameter limit.
- Relations were reduced to fewer but more diverse types to encourage targeted systems.

Similarity:

- COVER-KBC addresses the same fundamental issue: output materialization under zero/one/many cardinality and no known answer count.

Difference:

- COVER-KBC 2026 outputs strings and numeric values under the current benchmark; 2024 included disambiguated entities/Wikidata IDs.

### Navigating Nulls, Numbers and Numerous Entities

Verified source:

- Arunav Das, Nadeen Fathallah, Nicole Obretincheva. "Navigating Nulls, Numbers and Numerous Entities: Robust Knowledge Base Construction from Large Language Models." CEUR Vol. 3853, 2024. URL: https://ceur-ws.org/Vol-3853/paper12.pdf

Method summary:

- Uses prompt-engineering strategies tailored to nulls, numeric data, and one-to-many relations.
- Uses role-play and context-aware prompting.
- Uses Llama-3-8B-Instruct according to the paper text.
- Reports macro F1 0.653 in LM-KBC 2024.

Similarity:

- The title and method are directly aligned with the current COVER-KBC relation types: nulls, numbers, and numerous entities.
- It is strong evidence that relation-specific prompting is an established LM-KBC idea.

Difference:

- COVER-KBC has an evidence graph, blind verifier, scoring, residual coverage, controller, and final selectors; it is not just a fusion of prompt styles.
- COVER-KBC 2026 uses a single Mistral-Small checkpoint and Profile E3 final specializations.

Safe positioning sentence:

> Earlier LM-KBC work tailored prompts to null, numeric, and one-to-many relations; COVER-KBC retains this relation specificity but makes it executable through contracts, evidence state, verification, and adaptive stopping.

### Prompting as Probing

Verified source:

- Dimitrios Alivanistos, Selene Baez Santamaria, Michael Cochez, Jan-Christoph Kalo, Emile van Krieken, Thiviyan Thanapalasingam. "Prompting as Probing: Using Language Models for Knowledge Base Construction." CEUR Vol. 3274, 2022, pp. 11-34. URL: https://ceur-ws.org/Vol-3274/paper2.pdf

Method summary:

- Uses GPT-3 for LM-KBC 2022 Track 2.
- Multi-step prompting.
- Uses few-shot examples, variable answer list lengths, empty examples where possible, list formatting, and fact probing with true/false questions.
- Emphasizes manual prompt curation and alias dictionaries.

Similarity:

- Early precedent for multi-step closed-book KBC from LMs and for checking generated claims.

Difference:

- COVER-KBC formalizes these intuitions as typed relation contracts, evidence graph, verifier label probabilities, residual coverage, and adaptive control.

Safe positioning sentence:

> ProP established multi-step prompting and fact probing for LM-KBC; COVER-KBC extends this line with provenance-aware evidence state and budget-aware relation-typed control.

### Task-specific Pre-training and Prompt Decomposition

Verified source:

- Tianyi Li, Wenyu Huang, Nikos Papasarantopoulos, Pavlos Vougiouklis, Jeff Z. Pan. "Task-specific Pre-training and Prompt Decomposition for Knowledge Graph Population with Language Models." CEUR Vol. 3274, 2022, pp. 35-45. URL: https://ceur-ws.org/Vol-3274/paper3.pdf

Method summary:

- Track 1 winner in 2022.
- Uses task-specific pre-training of BERT with silver Wikidata data.
- Uses prompt decomposition for candidate generation.
- Uses adaptive thresholds for candidate selection.

Similarity:

- Prompt decomposition and adaptive thresholds are relevant precedents for relation-specific control.

Difference:

- COVER-KBC does not use task-specific pre-training or external silver triples.
- COVER-KBC uses generative Mistral inference and graph evidence rather than BERT masked-token candidate ranking.

## 5. Relation-Aware / Schema-Aware Inference

The closest relation-aware work is in LM-KBC itself:

- ReWiSe chooses aggregation strategy by relation schema.
- He and Razniewski route award to divide-and-conquer and other relations to Self-RAG-like internal-description extraction.
- Sam uses relation-specific system/user prompts.
- Navigating Nulls, Numbers and Numerous Entities explicitly chooses prompt strategies for null, numeric, and one-to-many relations.
- ProP uses relation-specific few-shot examples and fact probing.

General decomposition/reasoning references:

- Chain-of-thought prompting shows that explicit intermediate reasoning can improve reasoning tasks.
- Least-to-Most prompting decomposes complex tasks into subproblems.
- ReAct frames LLM inference as reasoning plus action, but it is built around external environment actions in tasks such as QA and embodied/web tasks.

How COVER-KBC differs:

- The mapping `relation -> contract -> view set -> parser -> verifier policy -> stopping policy -> selector` is explicit and executable.
- Relation adaptation is not only prompt wording; it changes evidence accounting, action legality, stopping, and output finalization.
- Unlike trainable relation-specific prompts, COVER-KBC remains training-free.

Do not claim:

- first relation-aware KBC system.
- first relation-specific prompting.
- first typed prompting.

Defensible claim:

- COVER-KBC operationalizes relation adaptation as an executable inference contract rather than only as relation-specific prompt templates or validation-chosen aggregation rules.

## 6. Self-Consistency and Multi-View Elicitation

Foundational references:

- Xuezhi Wang et al. "Self-Consistency Improves Chain of Thought Reasoning in Language Models." ICLR 2023. Metadata verified from ML Anthology/DBLP; OpenReview was blocked by browser verification during this review.
- Jason Wei et al. "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models." NeurIPS 2022.
- ReWiSe adapts self-consistency to LM-KBC relation schemas.

Standard self-consistency pattern:

```text
sample multiple reasoning paths
extract final answers
vote or aggregate
```

COVER-KBC core pattern:

```text
typed action
  -> parsed observation
  -> provenance-aware evidence graph
  -> candidate score / verifier evidence / residual coverage
  -> adaptive next action or stop
  -> relation-specific selector
```

Profile E3 numeric Multi-View pattern:

```text
four semantic deterministic views
  -> strict numeric parser
  -> tolerance cluster
  -> support >= 3 accept
  -> otherwise one source-blind judge
  -> fallback
```

Safe distinction:

- Multi-View itself is not novel.
- The current numeric specialization is a conservative, relation-specific adaptation of multi-view factual elicitation with strict parsers and tolerance-aware consensus.
- The core COVER-KBC contribution is the broader evidence/control architecture into which such views can be inserted.

## 7. Verification and Evidence Aggregation

Relevant verification literature:

- ProP uses fact probing with true/false questions over generated claims.
- Judge, Generator, Executioner uses the LLM as both generator and judge with numeric 0-100 ratings.
- SelfCheckGPT uses stochastic consistency among sampled responses to detect hallucinations without external resources.
- Calibrate Before Use provides the contextual calibration precedent for estimating answer-label biases with content-free inputs.

COVER-KBC verifier specifics:

- Candidate-level blind verification.
- Label space is `VALID`, `INVALID`, `UNKNOWN`.
- Verifier receives subject, relation definition, and candidate; it does not see generator reasoning.
- Uses label probabilities/logits, margin, entropy, and prompt disagreement.
- Verification becomes evidence, score features, hard gates, and controller state.

Evidence aggregation references:

- Knowledge Vault is a classic probabilistic knowledge fusion system combining sources and calibrated fact correctness probabilities.
- Truth discovery and knowledge fusion literature is conceptually related but not direct because COVER-KBC's sources are generated views from one model, not independent web sources.
- SelfCheckGPT is closer for black-box generated-observation consistency, but it is sentence-level hallucination detection rather than KB candidate materialization.

Safe distinction:

> COVER-KBC uses LLM verification, but unlike unrestricted judge prompting it stores verifier outcomes as typed evidence with provenance and keeps support, contradiction, logit confidence, and disagreement as separate signals.

Do not claim:

- first LLM verifier.
- first self-verification system.
- first evidence graph for LLM reasoning.

## 8. Adaptive Inference, Coverage, and Stopping

Relevant adaptive/test-time compute literature:

- ReAct: reasoning plus acting with an environment and observations.
- FLARE: active retrieval decides when and what to retrieve during generation based on low-confidence predictions.
- Self-RAG: trains a model to retrieve, generate, and critique with reflection tokens and on-demand retrieval.
- ReWiSe analyzes saturation of self-consistency samples by relation.
- He and Razniewski compare token/time costs and relation-specific strategy tradeoffs.

COVER-KBC controller:

```text
state = evidence graph + candidate scores + residual coverage + budget
actions = run view, run facet, verify, adversarial verify, reverse check, resample, stop
policy = deterministic action utility and hard legality/budget filters
termination = budget exhaustion, mandatory-view completion, residual threshold, relation-specific stopping
```

Coverage/completeness references:

- Razniewski, Arnaout, Ghosh, and Suchanek survey completeness, recall, and negation in open-world KBs.
- LM-KBC challenge overview papers stress that KBC requires materializing output sets under unknown cardinality.

Important distinction:

- Many LLM uncertainty methods ask "is this answer correct?"
- COVER-KBC also asks "is there likely useful evidence or a missing answer still worth searching for?"
- This is implemented as residual search need, not as a true cardinality estimator.

Safe distinction:

> COVER-KBC adapts test-time compute through a relation-typed controller whose stopping signal separates candidate confidence from residual search need.

Do not claim:

- first adaptive inference system.
- first coverage estimator.
- proven optimal stopping.
- probabilistic estimate of hidden remaining objects.

## 9. Numeric Elicitation

Numeric relations in COVER-KBC:

- `hasArea`: exact area in square kilometers, with country/island/lake/other semantic distinctions.
- `hasCapacity`: highest published maximum spectator capacity, with seated/total/standing/configuration ambiguity.

Related work:

- ReWiSe treats numeric relations as singleton numeric answers and compares majority, median, and average aggregation by relation.
- He and Razniewski normalize numeric answers to canonical digit forms.
- LM-KBC 2025 overview identifies `hasArea` and `hasCapacity` as difficult and reports low absolute F1 for most systems.

COVER-KBC distinction:

- Profile E3 uses semantic views, not only repeated samples.
- It uses strict `AREA:`/`CAPACITY:` parsers.
- It clusters with 5 percent relative tolerance, matching evaluation tolerance.
- It uses a source-blind judge only when no support-3 cluster exists.

Related Work space recommendation:

- Do not create a standalone numeric subsection in the final six-page paper.
- Mention numeric Multi-View in the self-consistency/multi-view subsection and in methodology/ablation.

## 10. Strongest Related Papers

Recommended citation candidates, ranked by relevance to COVER-KBC:

1. He and Razniewski 2025, Self-RAG + Divide-and-Conquer for LM-KBC.
2. Albert-Roulhac and Zouaq 2025, ReWiSe.
3. Kalo et al. 2025, LM-KBC 2025 overview.
4. Das, Fathallah, and Obretincheva 2024, Navigating Nulls, Numbers and Numerous Entities.
5. Alivanistos et al. 2022, Prompting as Probing.
6. Clay, Jimenez-Ruiz, and Madhyastha 2025, Judge, Generator, Executioner.
7. Wang et al. 2023, Self-Consistency.
8. Manakul, Liusie, and Gales 2023, SelfCheckGPT.
9. Jiang et al. 2023, Active Retrieval Augmented Generation/FLARE.
10. Asai et al. 2024, Self-RAG.
11. Zhao et al. 2021, Calibrate Before Use.
12. Razniewski et al. 2024, Completeness, Recall, and Negation in Open-world KBs.
13. Petroni et al. 2019, LAMA.
14. Singhania, Nguyen, and Razniewski 2022, LM-KBC overview.
15. Li et al. 2022, Task-specific Pre-training and Prompt Decomposition.
16. Creo et al. 2025, Soft Thinking.
17. Sam 2025, Qwen3-8B prompting baseline.
18. Dong et al. 2014, Knowledge Vault.

Likely final paper should cite 12-16 of these, not all 18.

## 11. Comparison Matrix

| Paper | Year | Area | Relation-aware? | Multi-generation? | Verification? | Adaptive inference? | Evidence state? | Completeness/coverage? | External retrieval? | Closest connection to COVER-KBC |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LM-KBC 2025 overview | 2025 | benchmark | Yes, relation categories | N/A | accept/reject task framing | N/A | N/A | cardinality task | No | Direct task context |
| ReWiSe | 2025 | LM-KBC system | Yes | Yes, `n_c=20` | aggregation only | Fixed sample budget | Entity aggregation | saturation analysis | No | Relation-wise self-consistency |
| Judge, Generator, Executioner | 2025 | LM-KBC system | Limited | Yes | LLM judge score | Iterative rerun | Candidate lists | No explicit residual | No | Generate-then-judge KBC |
| Qwen3-8B object prediction | 2025 | LM-KBC system | Yes, prompt templates | No/limited | Explored twice-prompt correction | Fixed | No | No | No | Relation-specific prompt baseline |
| Self-RAG + DaC | 2025 | LM-KBC system | Yes | Yes | validation/name filters | Strategy by relation, not per-row controller | Candidate aggregation | cost/coverage discussion | No external retrieval | Closest relation-aware hybrid |
| Soft Thinking | 2025 | LM-KBC system | Yes | Not central | No explicit judge | Fixed | No | No | No retrieval, but trains soft prompts | Trainable relation specialization contrast |
| LM-KBC 2024 overview | 2024 | benchmark | Yes, relation diversity | N/A | accept/reject task framing | N/A | N/A | cardinality task | Mostly self-contained track | Historical benchmark context |
| Navigating Nulls... | 2024 | LM-KBC system | Yes | Prompt fusion | Prompt/post-process | Fixed | No graph | Handles null/numeric/many | No evidence of RAG in cited abstract | Relation-specific prompt fusion |
| ProP | 2022 | LM-KBC system | Yes | Multi-step prompting | True/false fact probing | Fixed | No graph | empty-set examples | GPT-3, no current challenge constraints | Early multi-step KBC prompting |
| Li et al. prompt decomposition | 2022 | LM-KBC system | Yes | Prompt ensembles | threshold selection | Fixed/adaptive threshold | Candidate ranking | No residual | Uses Wikidata silver training | Decomposition and thresholds |
| LAMA | 2019 | probing | Relation templates | No | No materialization | No | No | No | No | Motivation for LM factual probing |
| Self-Consistency | 2023 | reasoning | No | Yes | vote/aggregate | Fixed sample budget | No | No | No | Multi-sample consensus background |
| SelfCheckGPT | 2023 | hallucination | No | Yes | consistency-based fact check | Fixed samples | sampled passages | No | No external DB | Zero-resource consistency checking |
| FLARE | 2023 | active RAG | No | Iterative | Low-confidence trigger | Yes | generation state | No set coverage | Yes | Adaptive trigger precedent |
| Self-RAG | 2024 | adaptive RAG | No | Yes | self-critique tokens | Yes, trained reflection | retrieval/generation state | No set coverage | Yes | On-demand action/critique contrast |
| Calibrate Before Use | 2021 | calibration | No | No | label calibration | No | No | No | No | Contextual calibration precedent |
| Completeness survey | 2024 | KB completeness | Schema/KG level | N/A | N/A | N/A | KB-level | Yes | N/A | Conceptual basis for coverage vs correctness |
| Knowledge Vault | 2014 | knowledge fusion | Schema-level | N/A | probabilistic fact correctness | No | source fusion | KB scale | Uses web/external sources | Evidence/probabilistic fusion background |

## 12. Detailed COVER-KBC vs Prior-Work Comparisons

### He and Razniewski 2025

SIMILARITY:

- Hybrid LM-only architecture for the same 2025 relation family.
- Relation type determines strategy.
- Internal descriptions substitute for external retrieval.
- Divide-and-conquer improves high-cardinality award enumeration.

DIFFERENCE:

- COVER-KBC has six relation contracts rather than a two-path award/other router.
- COVER-KBC records graph evidence, verification probabilities, residual coverage, and budgeted action decisions.
- COVER-KBC's current E3 award final layer is deterministic cleanup, not DaC.

SAFE POSITIONING:

> The closest prior LM-KBC system-level precedent is the Self-RAG + Divide-and-Conquer hybrid, which also argues for relation-dependent extraction strategies; COVER-KBC extends this into a typed evidence/control framework with relation-specific selectors.

### ReWiSe

SIMILARITY:

- Closed-book LM-KBC.
- Relation-wise strategy selection.
- Multi-generation aggregation.
- Numeric aggregation relation-specific.

DIFFERENCE:

- Fixed sampling and aggregation vs adaptive evidence acquisition.
- Synthetic CoT examples vs no training/fine-tuning.
- No evidence graph or blind verifier state.

SAFE POSITIONING:

> ReWiSe validates relation-wise self-consistency, while COVER-KBC treats multiple views as one action type inside a broader evidence and controller architecture.

### Judge, Generator, Executioner

SIMILARITY:

- Candidate generation plus LLM-based judging.
- Closed-book.
- Consensus/filtering.

DIFFERENCE:

- Free-form 0-100 scoring vs calibrated label probabilities.
- Judge sees generated triple process; COVER-KBC verifier is blind to generator reasoning.
- COVER-KBC stores verifier results as typed graph evidence.

SAFE POSITIONING:

> COVER-KBC shares the generation-verification motivation but avoids collapsing verification into a single judge score.

### Navigating Nulls, Numbers and Numerous Entities

SIMILARITY:

- Relation classes: nulls, numbers, many entities.
- Relation-tailored prompting.

DIFFERENCE:

- COVER-KBC encodes relation differences in contracts, selection policies, evidence graph, and stopping rules.

SAFE POSITIONING:

> Prior LM-KBC systems tailored prompts to null, numeric, and high-cardinality relations; COVER-KBC makes those distinctions executable throughout the inference graph.

### ProP

SIMILARITY:

- Multi-step prompting.
- Empty-set examples.
- Fact probing.
- Alias handling.

DIFFERENCE:

- COVER-KBC uses a structured evidence graph and closed-book open-weight Mistral runtime.
- COVER-KBC uses relation-specific residual stopping and selectors.

SAFE POSITIONING:

> ProP is an early LM-KBC demonstration of multi-step prompting and fact probing; COVER-KBC generalizes these ideas into typed evidence acquisition and control.

### SelfCheckGPT

SIMILARITY:

- Uses consistency of multiple sampled/generative outputs as a signal of factuality.
- Zero-resource/no external DB.

DIFFERENCE:

- SelfCheckGPT detects hallucinated sentences/passages; COVER-KBC materializes KB object sets.
- COVER-KBC combines consistency with relation contracts, verifiers, parsers, and selectors.

SAFE POSITIONING:

> Consistency among generated observations is related to black-box hallucination detection, but COVER-KBC uses it as one component in KB candidate materialization.

### FLARE and Self-RAG

SIMILARITY:

- Adaptive decision about additional inference.
- Critique/confidence can determine whether to act again.

DIFFERENCE:

- They retrieve external passages; COVER-KBC remains closed-book.
- COVER-KBC actions are relation-contract elicitation/verification actions, not retrieval actions.
- COVER-KBC's residual is answer-set search need, not low-confidence next-token generation.

SAFE POSITIONING:

> Active RAG work motivates adaptive inference, but COVER-KBC adapts compute without external retrieval by choosing among closed-book elicitation and verification actions.

### Completeness survey

SIMILARITY:

- Separates correctness of known facts from recall/completeness of a KB.

DIFFERENCE:

- The survey addresses open-world KBs broadly, not LLM test-time inference.
- COVER-KBC implements a heuristic residual search-need signal, not a formal completeness guarantee.

SAFE POSITIONING:

> COVER-KBC operationalizes a lightweight test-time analogue of KB completeness concerns by separating candidate confidence from residual search need.

## 13. Recommended Related Work Structure

Recommended number of subsections: 3.

### 2.1 Closed-Book Knowledge Base Construction and LM-KBC

Main argument:

- LM-KBC differs from classic probing because systems must materialize object sets under zero/one/many cardinality and make accept/reject decisions.
- Prior LM-KBC systems already used relation-specific prompting, self-consistency, judges, divide-and-conquer, and prompt tuning.
- COVER-KBC should be positioned as a 2026 closed-book, one-checkpoint, relation-typed evidence/control system.

Papers:

- Petroni et al. 2019 LAMA.
- LM-KBC 2022, 2024, 2025 overviews.
- ProP 2022.
- Navigating Nulls 2024.
- ReWiSe 2025.
- Self-RAG + DaC 2025.
- Judge, Generator, Executioner 2025.
- Soft Thinking 2025 as contrast.

Target length: 200-250 words.

Transition:

> Because prior LM-KBC systems already show that relation-specific prompting and repeated generation help, the next question is how to represent and verify the evidence produced by these calls.

### 2.2 Multi-View Elicitation, Verification, and Evidence Aggregation

Main argument:

- Self-consistency and prompt ensembling motivate repeated factual elicitation, but COVER-KBC stores parsed observations as evidence rather than only voting.
- LLM-as-judge and self-checking motivate verification, but COVER-KBC uses blind label-scored candidate verification.
- Knowledge fusion provides background for provenance-aware support/contradiction, but COVER-KBC sources are closed-book elicitation views.

Papers:

- Self-Consistency 2023.
- ReWiSe 2025.
- SelfCheckGPT 2023.
- Judge, Generator, Executioner 2025.
- Calibrate Before Use 2021.
- Knowledge Vault 2014, if space allows.

Target length: 150-220 words.

Transition:

> Once evidence is represented explicitly, the system can allocate additional inference only when the current state indicates unresolved candidates or residual missingness.

### 2.3 Adaptive Evidence Acquisition and Relation-Aware Finalization

Main argument:

- Adaptive inference/test-time compute methods decide whether additional model calls are useful.
- RAG systems such as FLARE and Self-RAG adaptively retrieve, but COVER-KBC remains closed-book and chooses among relation-specific elicitation and verification actions.
- KB completeness literature motivates the distinction between candidate correctness and missing answer coverage.
- COVER-KBC finalization is relation-aware: null-single, numeric, small-set, large-open-set, plus Profile E3 conservative repairs.

Papers:

- FLARE 2023.
- Self-RAG 2024.
- ReAct 2023, if space allows.
- Completeness survey 2024.
- He and Razniewski 2025 relation-strategy cost/coverage analysis.

Target length: 150-220 words.

## 14. Draft Content Blocks

### Subsection 1: Closed-Book Knowledge Base Construction and LM-KBC

Core argument:

LM-KBC is not ordinary factual probing. It requires materialized KB output under unknown answer cardinality, including empty answers and set-valued relations.

Papers to cite:

- LAMA.
- LM-KBC 2022 overview.
- LM-KBC 2024 overview.
- LM-KBC 2025 overview.
- ProP.
- Navigating Nulls.
- ReWiSe.
- Self-RAG + DaC.
- Soft Thinking as contrast.

Paper-by-paper relationship:

- LAMA motivates parametric factual probing but does not require set materialization.
- LM-KBC overviews define the benchmark transition from probing to KB construction.
- ProP introduces multi-step prompting and fact probing.
- Navigating Nulls shows relation-tailored prompting for null/numeric/many relations.
- ReWiSe adds relation-wise self-consistency.
- Self-RAG + DaC provides a relation-dependent hybrid system.
- Soft Thinking learns relation-specific prompt embeddings, contrasting with COVER-KBC's training-free explicit contracts.

Key contrast with COVER-KBC:

COVER-KBC makes relation adaptation executable beyond prompt wording.

Potential prose points:

- "COVER-KBC follows the LM-KBC line but treats each relation as a typed inference program."
- "The system remains closed-book and training-free, in contrast to soft-prompt or retrieval-heavy variants."

Claims to avoid:

- "first relation-aware LM-KBC system."
- "first closed-book KBC system."

### Subsection 2: Multi-View Elicitation, Verification, and Evidence Aggregation

Core argument:

Multiple prompts and samples can improve factual elicitation, but COVER-KBC differs by preserving provenance, support, contradiction, verifier probabilities, and disagreement.

Papers to cite:

- Self-Consistency.
- ReWiSe.
- SelfCheckGPT.
- Judge, Generator, Executioner.
- Calibrate Before Use.
- Knowledge Vault if room.

Paper-by-paper relationship:

- Self-consistency motivates sampling diverse answers and aggregating.
- ReWiSe applies that idea to LM-KBC relation schemas.
- SelfCheckGPT shows black-box consistency as a factuality signal.
- Judge, Generator, Executioner shows LM-as-judge for KBC.
- Calibrate Before Use motivates content-free calibration for label probabilities.
- Knowledge Vault gives a broader evidence-fusion precedent.

Key contrast with COVER-KBC:

COVER-KBC uses strict parsers and graph evidence; it does not simply vote strings or average judge scores.

Potential prose points:

- "Our verifier is blind and label-scored, producing `VALID`, `INVALID`, `UNKNOWN` probabilities."
- "Support, contradiction, and disagreement are separate features in candidate scoring."

Claims to avoid:

- "first self-verifying KBC system."
- "first evidence graph for LLMs."

### Subsection 3: Adaptive Evidence Acquisition and Relation-Aware Finalization

Core argument:

The system spends test-time compute adaptively under relation-specific stopping rules, using residual coverage rather than fixed numbers of samples.

Papers to cite:

- FLARE.
- Self-RAG.
- ReAct if space allows.
- Completeness survey.
- Self-RAG + DaC cost/strategy analysis.

Paper-by-paper relationship:

- FLARE and Self-RAG decide when retrieval/action is needed, but use external retrieval/trained reflection.
- ReAct gives a reasoning-action-observation framing.
- The completeness survey motivates distinguishing correctness from missingness/coverage.
- He and Razniewski show relation-dependent cost and decomposition tradeoffs in LM-KBC.

Key contrast with COVER-KBC:

COVER-KBC is closed-book; its actions are elicitation/verification actions over one Mistral checkpoint, and its residual is a heuristic search-need signal.

Potential prose points:

- "Unlike fixed-sample self-consistency, COVER-KBC's controller can stop early or spend additional calls based on relation-specific residual signals."
- "The final selector is relation-aware, not a uniform vote."

Claims to avoid:

- "optimal stopping."
- "first adaptive LLM inference."
- "formal completeness estimator."

## 15. Claims We Should Avoid

Do not make these claims in Related Work:

- "COVER-KBC is the first relation-aware LLM KBC system."
- "COVER-KBC is the first multi-view KBC method."
- "COVER-KBC is the first system to use self-consistency for KBC."
- "COVER-KBC is the first LLM verifier/judge system."
- "COVER-KBC is the first agentic KBC system."
- "COVER-KBC proves coverage/completeness of generated KB rows."
- "COVER-KBC is a retrieval-augmented generation system."
- "COVER-KBC trains a new model or learns prompts."
- "COVER-KBC's residual coverage is a probability of remaining true objects."
- "Profile E3's Area/Capacity Multi-View is the whole architecture."

Prefer these formulations:

- "Unlike fixed-sample self-consistency..."
- "In contrast to prompt-only relation adaptation..."
- "COVER-KBC operationalizes relation adaptation through executable contracts..."
- "COVER-KBC combines..."
- "The system separates candidate confidence from residual search need..."
- "The final Profile E3 layer is a conservative relation-specific specialization..."

## 16. Verified BibTeX Candidates

Only entries with metadata verified from accessible sources are marked `VERIFIED`. Entries whose canonical OpenReview page was blocked are marked `NEEDS MANUAL CHECK`, even when metadata was cross-checked from another source.

### VERIFIED: LM-KBC 2025 overview

```bibtex
@inproceedings{kalo2025lmkbc,
  title = {{LM-KBC} 2025: 4th Challenge on Knowledge Base Construction from Pre-trained Language Models},
  author = {Kalo, Jan-Christoph and Razniewski, Simon and Zhang, Bohui and Nguyen, Tuan-Phong},
  booktitle = {Joint Proceedings of the 3rd Workshop on Knowledge Base Construction from Pre-Trained Language Models and the 4th Challenge on Language Models for Knowledge Base Construction (KBC-LM+LM-KBC 2025)},
  series = {CEUR Workshop Proceedings},
  volume = {4041},
  year = {2025},
  url = {https://ceur-ws.org/Vol-4041/paper7.pdf}
}
```

### VERIFIED: ReWiSe

```bibtex
@inproceedings{albertRoulhac2025rewise,
  title = {{ReWiSe}: Relation-Wise Self-consistency for LLM Probing},
  author = {Albert-Roulhac, Edouard and Zouaq, Amal},
  booktitle = {Joint Proceedings of the 3rd Workshop on Knowledge Base Construction from Pre-Trained Language Models and the 4th Challenge on Language Models for Knowledge Base Construction (KBC-LM+LM-KBC 2025)},
  series = {CEUR Workshop Proceedings},
  volume = {4041},
  year = {2025},
  url = {https://ceur-ws.org/Vol-4041/paper8.pdf}
}
```

### VERIFIED: Judge, Generator, Executioner

```bibtex
@inproceedings{clay2025judge,
  title = {Judge, Generator, Executioner: Utilizing an LLM for KBC},
  author = {Clay, Alex and Jimenez-Ruiz, Ernesto and Madhyastha, Pranava},
  booktitle = {Joint Proceedings of the 3rd Workshop on Knowledge Base Construction from Pre-Trained Language Models and the 4th Challenge on Language Models for Knowledge Base Construction (KBC-LM+LM-KBC 2025)},
  series = {CEUR Workshop Proceedings},
  volume = {4041},
  year = {2025},
  url = {https://ceur-ws.org/Vol-4041/paper9.pdf}
}
```

### VERIFIED: Qwen3-8B LM-KBC 2025 system

```bibtex
@inproceedings{sam2025qwen,
  title = {Examining 4-bit quantized Qwen3-8B for object prediction @ LM-KBC 2025},
  author = {Sam, Irene Mary},
  booktitle = {Joint Proceedings of the 3rd Workshop on Knowledge Base Construction from Pre-Trained Language Models and the 4th Challenge on Language Models for Knowledge Base Construction (KBC-LM+LM-KBC 2025)},
  series = {CEUR Workshop Proceedings},
  volume = {4041},
  year = {2025},
  url = {https://ceur-ws.org/Vol-4041/paper10.pdf}
}
```

### VERIFIED: Self-RAG + Divide-and-Conquer LM-KBC 2025 system

```bibtex
@inproceedings{he2025selfragdac,
  title = {Combining Self-Retrieval-Augmented Generation with Divide-and-Conquer for Language Model-based Knowledge Base Construction},
  author = {He, Jingbo and Razniewski, Simon},
  booktitle = {Joint Proceedings of the 3rd Workshop on Knowledge Base Construction from Pre-Trained Language Models and the 4th Challenge on Language Models for Knowledge Base Construction (KBC-LM+LM-KBC 2025)},
  series = {CEUR Workshop Proceedings},
  volume = {4041},
  year = {2025},
  url = {https://ceur-ws.org/Vol-4041/paper11.pdf}
}
```

### VERIFIED: Soft Thinking

```bibtex
@inproceedings{creo2025softthinking,
  title = {Soft Thinking: Enhancing Knowledge Base Completion through Trainable Prompts in the Chain of Thought},
  author = {Creo, Aldan and Gueret, Christophe and Bernardi, Alberto and Janik, Adrianna and Costabello, Luca},
  booktitle = {Joint Proceedings of the 3rd Workshop on Knowledge Base Construction from Pre-Trained Language Models and the 4th Challenge on Language Models for Knowledge Base Construction (KBC-LM+LM-KBC 2025)},
  series = {CEUR Workshop Proceedings},
  volume = {4041},
  year = {2025},
  url = {https://ceur-ws.org/Vol-4041/paper12.pdf}
}
```

### VERIFIED: LM-KBC 2024 overview

```bibtex
@inproceedings{kalo2024preface,
  title = {Preface: {LM-KBC} Challenge 2024},
  author = {Kalo, Jan-Christoph and Nguyen, Tuan-Phong and Razniewski, Simon and Zhang, Bohui},
  booktitle = {Joint Proceedings of the KBC-LM Workshop and the LM-KBC Challenge 2024},
  series = {CEUR Workshop Proceedings},
  volume = {3853},
  year = {2024},
  url = {https://ceur-ws.org/Vol-3853/paper0.pdf}
}
```

### VERIFIED: Navigating Nulls, Numbers and Numerous Entities

```bibtex
@inproceedings{das2024navigating,
  title = {Navigating Nulls, Numbers and Numerous Entities: Robust Knowledge Base Construction from Large Language Models},
  author = {Das, Arunav and Fathallah, Nadeen and Obretincheva, Nicole},
  booktitle = {Joint Proceedings of the KBC-LM Workshop and the LM-KBC Challenge 2024},
  series = {CEUR Workshop Proceedings},
  volume = {3853},
  year = {2024},
  url = {https://ceur-ws.org/Vol-3853/paper12.pdf}
}
```

### VERIFIED: LM-KBC 2022 overview

```bibtex
@inproceedings{singhania2022lmkbc,
  title = {{LM-KBC}: Knowledge Base Construction from Pre-trained Language Models},
  author = {Singhania, Sneha and Nguyen, Tuan-Phong and Razniewski, Simon},
  booktitle = {Proceedings of the Semantic Web Challenge on Knowledge Base Construction from Pre-trained Language Models 2022},
  series = {CEUR Workshop Proceedings},
  volume = {3274},
  pages = {1--10},
  year = {2022},
  url = {https://ceur-ws.org/Vol-3274/paper1.pdf}
}
```

### VERIFIED: Prompting as Probing

```bibtex
@inproceedings{alivanistos2022prompting,
  title = {Prompting as Probing: Using Language Models for Knowledge Base Construction},
  author = {Alivanistos, Dimitrios and Baez Santamaria, Selene and Cochez, Michael and Kalo, Jan-Christoph and van Krieken, Emile and Thanapalasingam, Thiviyan},
  booktitle = {Proceedings of the Semantic Web Challenge on Knowledge Base Construction from Pre-trained Language Models 2022},
  series = {CEUR Workshop Proceedings},
  volume = {3274},
  pages = {11--34},
  year = {2022},
  url = {https://ceur-ws.org/Vol-3274/paper2.pdf}
}
```

### VERIFIED: Task-specific Pre-training and Prompt Decomposition

```bibtex
@inproceedings{li2022taskspecific,
  title = {Task-specific Pre-training and Prompt Decomposition for Knowledge Graph Population with Language Models},
  author = {Li, Tianyi and Huang, Wenyu and Papasarantopoulos, Nikos and Vougiouklis, Pavlos and Pan, Jeff Z.},
  booktitle = {Proceedings of the Semantic Web Challenge on Knowledge Base Construction from Pre-trained Language Models 2022},
  series = {CEUR Workshop Proceedings},
  volume = {3274},
  pages = {35--45},
  year = {2022},
  url = {https://ceur-ws.org/Vol-3274/paper3.pdf}
}
```

### VERIFIED: LAMA

```bibtex
@inproceedings{petroni2019language,
  title = {Language Models as Knowledge Bases?},
  author = {Petroni, Fabio and Rocktaschel, Tim and Riedel, Sebastian and Lewis, Patrick and Bakhtin, Anton and Wu, Yuxiang and Miller, Alexander},
  booktitle = {Proceedings of the 2019 Conference on Empirical Methods in Natural Language Processing and the 9th International Joint Conference on Natural Language Processing (EMNLP-IJCNLP)},
  pages = {2463--2473},
  year = {2019},
  publisher = {Association for Computational Linguistics},
  doi = {10.18653/v1/D19-1250},
  url = {https://aclanthology.org/D19-1250/}
}
```

### VERIFIED: Chain-of-Thought Prompting

```bibtex
@inproceedings{wei2022chain,
  title = {Chain-of-Thought Prompting Elicits Reasoning in Large Language Models},
  author = {Wei, Jason and Wang, Xuezhi and Schuurmans, Dale and Bosma, Maarten and Ichter, Brian and Xia, Fei and Chi, Ed and Le, Quoc V. and Zhou, Denny},
  booktitle = {Advances in Neural Information Processing Systems},
  volume = {35},
  year = {2022},
  url = {https://proceedings.neurips.cc/paper_files/paper/2022/hash/9d5609613524ecf4f15af0f7b31abca4-Abstract.html}
}
```

### NEEDS MANUAL CHECK: Self-Consistency

OpenReview metadata was blocked by browser verification. Metadata below was verified from ML Anthology and DBLP, but the final paper should re-check the OpenReview page.

```bibtex
@inproceedings{wang2023selfconsistency,
  title = {Self-Consistency Improves Chain of Thought Reasoning in Language Models},
  author = {Wang, Xuezhi and Wei, Jason and Schuurmans, Dale and Le, Quoc V. and Chi, Ed H. and Narang, Sharan and Chowdhery, Aakanksha and Zhou, Denny},
  booktitle = {International Conference on Learning Representations},
  year = {2023},
  url = {https://mlanthology.org/iclr/2023/wang2023iclr-selfconsistency/}
}
```

### VERIFIED: SelfCheckGPT

```bibtex
@inproceedings{manakul2023selfcheckgpt,
  title = {{SelfCheckGPT}: Zero-Resource Black-Box Hallucination Detection for Generative Large Language Models},
  author = {Manakul, Potsawee and Liusie, Adian and Gales, Mark},
  booktitle = {Proceedings of the 2023 Conference on Empirical Methods in Natural Language Processing},
  pages = {9004--9017},
  year = {2023},
  publisher = {Association for Computational Linguistics},
  doi = {10.18653/v1/2023.emnlp-main.557},
  url = {https://aclanthology.org/2023.emnlp-main.557/}
}
```

### VERIFIED: Active Retrieval Augmented Generation

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

### NEEDS MANUAL CHECK: ReAct

OpenReview was blocked. Metadata below was cross-checked from the official GitHub citation and should be verified against OpenReview before final BibTeX use.

```bibtex
@inproceedings{yao2023react,
  title = {{ReAct}: Synergizing Reasoning and Acting in Language Models},
  author = {Yao, Shunyu and Zhao, Jeffrey and Yu, Dian and Du, Nan and Shafran, Izhak and Narasimhan, Karthik and Cao, Yuan},
  booktitle = {International Conference on Learning Representations},
  year = {2023},
  url = {https://arxiv.org/abs/2210.03629}
}
```

### NEEDS MANUAL CHECK: Self-RAG

OpenReview was blocked. Metadata below was verified from IBM Research and the official GitHub citation, but should be re-checked against OpenReview.

```bibtex
@inproceedings{asai2024selfrag,
  title = {{Self-RAG}: Learning to Retrieve, Generate, and Critique through Self-Reflection},
  author = {Asai, Akari and Wu, Zeqiu and Wang, Yizhong and Sil, Avirup and Hajishirzi, Hannaneh},
  booktitle = {The Twelfth International Conference on Learning Representations},
  year = {2024},
  url = {https://openreview.net/forum?id=hSyW5go0v8}
}
```

### VERIFIED: Completeness, Recall, and Negation Survey

```bibtex
@article{razniewski2024completeness,
  title = {Completeness, Recall, and Negation in Open-world Knowledge Bases: A Survey},
  author = {Razniewski, Simon and Arnaout, Hiba and Ghosh, Shrestha and Suchanek, Fabian M.},
  journal = {ACM Computing Surveys},
  volume = {56},
  number = {6},
  article = {150},
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

## 17. Missing or Unverified References

References that may be useful but need manual verification before final citation:

- Least-to-Most Prompting Enables Complex Reasoning in Large Language Models. DBLP confirms ICLR 2023 metadata, but OpenReview was blocked during this review.
- Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. Relevant mainly as contrast because COVER-KBC is closed-book; not essential for final Related Work unless Self-RAG/FLARE context needs it.
- Tree of Thoughts. Relevant only if the final paper emphasizes search/planning; otherwise too broad.
- LLM-as-a-Judge survey or MT-Bench/Chatbot Arena. Useful background for judge models but not necessary if Judge, Generator, Executioner and SelfCheckGPT are cited.
- Prompt Ensembles for LM-KBC 2023. Relevant if the final paper needs a direct prompt-ensemble contrast; otherwise ReWiSe covers the closer LM-KBC self-consistency comparison.
- LLM2KB 2023. Relevant as a RAG/fine-tuning contrast, but current COVER-KBC should not spend much space on retrieval-heavy systems.

Final Related Work should prioritize papers that directly clarify what COVER-KBC is not:

- not LAMA-style single-token probing,
- not fixed-sample self-consistency,
- not unrestricted LLM-as-judge reranking,
- not RAG,
- not trainable prompt tuning,
- not only prompt templates.

The most defensible distinction is:

> COVER-KBC combines relation-typed contracts, provenance-aware evidence state, blind calibrated verification, residual coverage estimation, budget-aware adaptive control, relation-aware selection, and conservative Profile E3 specialization in a single closed-book one-checkpoint LM-KBC system.

