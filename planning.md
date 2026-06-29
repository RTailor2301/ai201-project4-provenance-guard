# Provenance Guard — Planning Document

## Problem Statement

Creative platform classification of AI vs human generated content. Provenance Guard acts as a backend service that classifies an item with confidence scores, labels, appeals, rate limiting, and audit log.

---

## Architecture Narrative

When a creator submits a piece of text, the following path occurs:

1. **Client → POST /submit** — The platform sends the raw text (and optional creator metadata) to Provenance Guard.
2. **Rate Limiter** — Flask-Limiter checks whether this IP has exceeded the submission quota. If yes, return 429. If no, proceed.
3. **Content Store** — The submission is saved to SQLite with a unique `content_id`, status `classified`, and the raw text.
4. **Signal 1: LLM Classifier (Groq)** — The text is sent to Groq (llama-3.3-70b-versatile) with a structured prompt asking whether the writing reads as human-authored or AI-generated. Returns a score from 0.0 (confident human) to 1.0 (confident AI).
5. **Signal 2: Stylometric Heuristics** — Pure-Python analysis computes structural statistics: sentence-length variance, type-token ratio (vocabulary diversity), and punctuation density. These are combined into a single score from 0.0 (human-like variability) to 1.0 (AI-like uniformity).
6. **Confidence Scorer** — The two signal scores are weighted and combined into a final `ai_probability` (0.0–1.0) and a `confidence` score (0.0–1.0) that reflects how much the signals agree. Disagreement lowers confidence even if one signal is strong.
7. **Label Generator** — Based on `ai_probability` and `confidence`, one of three transparency label variants is selected (high-confidence AI, uncertain, high-confidence human).
8. **Audit Logger** — A structured JSON entry is written to SQLite recording: timestamp, content_id, both signal scores, combined score, confidence, attribution result, label text, and status.
9. **Response** — The API returns a JSON object with `content_id`, attribution result, confidence score, individual signal scores, and the transparency label text.

**Appeal flow:**

1. **Client → POST /appeal** — A creator sends the `content_id` and their reasoning for why the classification is wrong.
2. **Content Store** — The content's status is updated from `classified` to `under_review`.
3. **Audit Logger** — A new audit entry is appended linking the appeal reasoning to the original classification entry (same `content_id`, event type `appeal`).
4. **Response** — Returns confirmation with updated status `under_review`.

A moderator (out of scope for v1) would later review appeals manually. Automated re-classification is not required.

---

## Architecture
Generated architecture of plan
```
┌─────────────┐     POST /submit      ┌──────────────┐
│   Platform  │ ────────────────────► │ Rate Limiter │
│   (client)  │                       └──────┬───────┘
└─────────────┘                              │ pass
                                             ▼
                                      ┌──────────────┐
                                      │ Content Store│ (SQLite)
                                      └──────┬───────┘
                                             │ raw text
                              ┌──────────────┼──────────────┐
                              ▼              ▼              │
                       ┌────────────┐ ┌──────────────┐     │
                       │ Signal 1   │ │ Signal 2     │     │
                       │ LLM (Groq) │ │ Stylometrics │     │
                       └─────┬──────┘ └──────┬───────┘     │
                             │ score 0-1    │ score 0-1   │
                             └──────┬───────┘             │
                                    ▼                      │
                             ┌──────────────┐              │
                             │  Confidence  │              │
                             │   Scorer     │              │
                             └──────┬───────┘              │
                                    │ ai_prob, confidence  │
                                    ▼                      │
                             ┌──────────────┐              │
                             │    Label     │              │
                             │  Generator   │              │
                             └──────┬───────┘              │
                                    │ label text           │
                                    ▼                      │
                             ┌──────────────┐◄─────────────┘
                             │  Audit Log   │ (SQLite JSON)
                             └──────┬───────┘
                                    │
                                    ▼
                              JSON response


Appeal flow:

┌─────────────┐     POST /appeal      ┌──────────────┐
│   Creator   │ ────────────────────► │ Content Store│ status → under_review
└─────────────┘   content_id + reason └──────┬───────┘
                                             │
                                             ▼
                                      ┌──────────────┐
                                      │  Audit Log   │ event: appeal
                                      └──────┬───────┘
                                             ▼
                                      JSON response
```

---

## Detection Signals

### Signal 1: LLM Semantic Classifier (Groq — llama-3.3-70b-versatile)

| Property | Detail |
|---|---|
| **What it measures** | Semantic coherence, tone consistency, and phrasing patterns that distinguish AI-generated prose from human writing. |
| **Why it differs** | LLMs produce text with high grammatical correctness and uniform tone; humans vary in rhythm, use idioms inconsistently, and sometimes write awkwardly. |
| **Output** | Score 0.0–1.0 where higher = more likely AI-generated. |
| **Blind spots** | Cannot detect AI text that was heavily edited by a human. May flag formal, polished human writing (academic essays, corporate copy) as AI. Cannot analyze content it wasn't trained to distinguish. |

### Signal 2: Stylometric Heuristics (Pure Python)

| Property | Detail |
|---|---|
| **What it measures** | Structural writing statistics: (a) sentence-length variance, (b) type-token ratio (unique words / total words), (c) punctuation density (punctuation marks per sentence). |
| **Why it differs** | AI text tends toward uniform sentence lengths, moderate vocabulary diversity, and predictable punctuation. Human writing is more variable — short punchy sentences mixed with long ones, wider or narrower vocabulary depending on the writer. |
| **Output** | Score 0.0–1.0 where higher = more AI-like structural uniformity. |
| **Blind spots** | A human who writes in a very uniform style (technical documentation, children's books) may score as AI-like. Very short texts (< 50 words) don't have enough sentences for reliable variance measurement. Non-English text is not calibrated. |

### How Signals Are Combined

```
ai_probability = (0.6 × llm_score) + (0.4 × stylometric_score)
confidence     = 1.0 - abs(llm_score - stylometric_score)
```

- **Weighting rationale:** The LLM signal gets 60% weight because it captures meaning-level patterns. Stylometrics get 40% as a structural cross-check.
- **Confidence:** When both signals agree (e.g., LLM=0.85, Stylometric=0.80), confidence is high (0.95). When they disagree (e.g., LLM=0.90, Stylometric=0.30), confidence drops (0.40), pushing the result toward the "uncertain" label regardless of the combined score.
- **False-positive bias:** When signals disagree and the LLM alone would flag AI, the low confidence prevents a harsh "AI-generated" label. The uncertain label is shown instead, giving the human writer benefit of the doubt.

---

## Confidence Scoring & Uncertainty

### What 0.5 Means

A confidence score of 0.5 means the two signals gave conflicting readings — the system genuinely does not know. It is not "50% sure it's AI." The transparency label will be the uncertain variant.

### Thresholds

| ai_probability | confidence | Attribution | Label variant |
|---|---|---|---|
| ≥ 0.75 | ≥ 0.60 | `likely_ai` | High-confidence AI |
| ≤ 0.25 | ≥ 0.60 | `likely_human` | High-confidence human |
| anything else, OR confidence < 0.60 | — | `uncertain` | Uncertain |

The confidence gate (< 0.60 → always uncertain) is the primary false-positive safeguard.

### Validation Plan

Before shipping, test with:
1. **Obvious AI text** — A ChatGPT-generated product description. Expect: high ai_probability, high confidence, "likely_ai" label.
2. **Obvious human text** — A raw, informal personal blog post with typos and varied sentence lengths. Expect: low ai_probability, high confidence, "likely_human" label.
3. **Edge case: polished human** — A formal essay. Expect: moderate ai_probability, lower confidence, "uncertain" label (signals may disagree).
4. **Edge case: short text** — A 20-word poem. Expect: low confidence due to insufficient data for stylometrics.

---

## Transparency Labels

Three label variants, written in plain language:

### High-Confidence AI (ai_probability ≥ 0.75, confidence ≥ 0.60)

> "This content was likely created with AI assistance. Our analysis found consistent patterns typical of AI-generated writing."

### High-Confidence Human (ai_probability ≤ 0.25, confidence ≥ 0.60)

> "This content appears to be original human writing. Our analysis found patterns consistent with human-authored text."

### Uncertain (everything else, or confidence < 0.60)

> "We couldn't determine how this content was created with high confidence. It may be human-written, AI-assisted, or AI-generated. If you believe this is incorrect, you can submit an appeal."

---

## False Positive Scenario

**Scenario:** A poet submits a carefully edited, formal sonnet. The LLM signal flags it as AI-like (0.82) because the language is polished and uniform. The stylometric signal gives 0.55 (moderate — sonnets have structural constraints that reduce variance).

**What happens:**
- `ai_probability` = (0.6 × 0.82) + (0.4 × 0.55) = 0.712
- `confidence` = 1.0 - |0.82 - 0.55| = 0.73
- Result: `ai_probability` is below 0.75 threshold → **uncertain** label, not "likely AI"
- The poet sees: *"We couldn't determine how this content was created with high confidence..."*
- The poet submits an appeal via POST /appeal with reasoning: "This is an original sonnet I wrote for a poetry workshop."
- Status changes to `under_review`. Both the original classification and the appeal appear in the audit log.

Even if ai_probability had crossed 0.75, the system avoids absolute language ("definitely AI") and always offers an appeal path.

---

## API Surface

### POST /submit

Submit text for attribution analysis.

**Request:**
```json
{
  "text": "The poem or story text to analyze...",
  "creator_id": "optional-creator-identifier"
}
```

**Response (200):**
```json
{
  "content_id": "uuid",
  "attribution": "likely_ai | likely_human | uncertain",
  "confidence": 0.73,
  "ai_probability": 0.71,
  "signals": {
    "llm_score": 0.82,
    "stylometric_score": 0.55
  },
  "transparency_label": "We couldn't determine how this content was created...",
  "status": "classified"
}
```

**Error (429):** Rate limit exceeded.

### POST /appeal

Contest a classification.

**Request:**
```json
{
  "content_id": "uuid-from-submit-response",
  "reason": "This is my original work. I wrote it for a poetry workshop."
}
```

**Response (200):**
```json
{
  "content_id": "uuid",
  "status": "under_review",
  "message": "Your appeal has been submitted and is under review."
}
```

**Error (404):** content_id not found.

### GET /log

Retrieve the structured audit log.

**Response (200):**
```json
{
  "entries": [
    {
      "timestamp": "2026-06-29T10:00:00Z",
      "event_type": "classification",
      "content_id": "abc-123",
      "attribution": "likely_human",
      "confidence": 0.91,
      "ai_probability": 0.12,
      "signals": { "llm_score": 0.10, "stylometric_score": 0.15 },
      "transparency_label": "This content appears to be original human writing...",
      "status": "classified"
    },
    {
      "timestamp": "2026-06-29T10:05:00Z",
      "event_type": "appeal",
      "content_id": "abc-123",
      "reason": "This is my original work...",
      "status": "under_review"
    }
  ]
}
```

### GET /health

Simple health check. Returns `{ "status": "ok" }`.

---

## Appeals Workflow

### Normal Flow
1. Creator receives a classification they disagree with.
2. Creator calls POST /appeal with content_id and free-text reasoning.
3. System validates content_id exists and status is `classified` (not already under review).
4. Status updated to `under_review`.
5. Appeal logged in audit log with reference to original classification.
6. (Out of scope) Moderator reviews manually and updates status to `appeal_granted` or `appeal_denied`.

### Edge Case 1: Duplicate Appeal
A creator submits a second appeal for the same content_id while status is already `under_review`.
- **Handling:** Return 409 Conflict with message "An appeal is already pending for this content."

### Edge Case 2: Appeal on Non-Existent Content
A creator submits an appeal with an invalid or expired content_id.
- **Handling:** Return 404 Not Found.

### Edge Case 3: Very Short Text Submitted for Classification
A creator submits text with fewer than 20 words.
- **Handling:** Process normally, but stylometric signal returns a low-confidence score (insufficient data). Combined confidence will likely fall below 0.60, producing the uncertain label automatically.

---

## Rate Limiting

| Limit | Value | Reasoning |
|---|---|---|
| Submissions per IP | 10 per minute | A typical creator submits 1–3 pieces when publishing. 10/min allows batch uploads while blocking automated flooding. |
| Submissions per IP | 100 per hour | Prevents sustained abuse from a single source over a longer window. |
| Appeals per IP | 5 per hour | Appeals are infrequent; 5/hour allows legitimate retries while preventing spam appeals. |

Implementation: Flask-Limiter with in-memory storage (sufficient for a single-process demo).

---

## Data Storage

SQLite with two tables:

**content** — stores submissions and current status.
**audit_log** — append-only JSON entries for every classification and appeal event.

No external database setup required.

---

## File Structure (Planned)

```
provenance-guard/
├── app.py              # Flask app, routes, rate limiting
├── detector.py         # Signal 1 (LLM) + Signal 2 (stylometrics)
├── scorer.py           # Confidence scoring + label generation
├── storage.py          # SQLite content store + audit log
├── requirements.txt
├── .env.example
├── planning.md
└── README.md
```

---

## AI Tool Plan

| Milestone | AI Tool Use | Input Provided | Expected Output |
|---|---|---|---|
| **Milestone 2: Core Implementation** | Generate Flask route boilerplate, SQLite schema, and stylometric heuristic functions | Architecture diagram, API surface spec, signal descriptions from this document | Working app.py, storage.py, detector.py skeletons — will review and adjust weights/thresholds manually |
| **Milestone 3: Integration & Testing** | Generate test curl commands and sample audit log entries for README demo section | API response schemas, threshold table, label variants | Demo commands and example JSON — will run each command myself and verify output matches spec |
| **Milestone 4: README & Docs** | Draft README sections (known limitations, spec reflection, AI usage log) | Completed implementation notes, threshold decisions, test results | README prose — will rewrite in my own voice and add specific examples from my actual testing |

**What I will NOT delegate to AI:**
- Threshold and weight decisions (0.6/0.4 split, 0.75/0.25 cutoffs) — these are design choices informed by the false-positive analysis above.
- Transparency label wording — will test phrasing with a non-technical reader before finalizing.
- Rate limit values — tied to realistic platform usage patterns, not defaults.
