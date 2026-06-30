# Provenance Guard

A backend service for creative platforms to classify text attribution (human vs. AI-generated), score confidence, surface transparency labels, handle creator appeals, and maintain a structured audit log.

See [planning.md](planning.md) for the full architecture diagram and design rationale.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your GROQ_API_KEY
python app.py          # runs on http://localhost:5000
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/submit` | POST | Submit text for attribution analysis |
| `/appeal` | POST | Contest a classification |
| `/log` | GET | Retrieve structured audit log |
| `/health` | GET | Health check |

### POST /submit

```bash
curl -s -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "Your text here...", "creator_id": "user-123"}' | python -m json.tool
```

### POST /appeal

```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "YOUR-CONTENT-ID", "creator_reasoning": "Why you believe this is wrong."}' | python -m json.tool
```

### GET /log

```bash
curl -s http://localhost:5000/log | python -m json.tool
```

---

## Architecture

A platform POSTs text to `/submit`. The request passes rate limiting, runs two independent detection signals (LLM + stylometrics), combines them into a confidence score, selects one of three transparency labels, writes a structured audit log entry, and returns the full result.

Appeal flow: a creator POSTs a `content_id` and reasoning to `/appeal`, which updates status to `under_review` and appends an appeal event to the audit log.

```
Platform → Rate Limiter → Content Store → Signal 1 (Groq LLM)
                                        → Signal 2 (Stylometrics)
                                        → Confidence Scorer → Label Generator → Audit Log → JSON response

Creator → POST /appeal → Content Store (status: under_review) → Audit Log → JSON response
```

Full diagram in [planning.md](planning.md).

---

## Detection Signals

### Why these two signals?

I chose one semantic signal and one structural signal because they capture genuinely different properties of text. A single approach — even a powerful LLM — can be fooled by edited AI output or flag polished human writing. Combining independent signals lets disagreement surface as uncertainty rather than a false verdict.

### Signal 1: LLM Semantic Classifier (Groq — llama-3.3-70b-versatile)

**What it measures:** Semantic coherence, tone consistency, and phrasing patterns — the "does this read like AI?" question at a meaning level.

**Why it helps:** LLM-generated text tends toward grammatical correctness, uniform tone, and predictable transitions ("Furthermore," "It is important to note"). Human writing is messier — typos, slang, uneven rhythm.

**Output:** A score from 0.0 (confident human) to 1.0 (confident AI).

**What it misses:** Heavily edited AI text. Formal human writing (academic essays, corporate copy) that happens to sound polished. Anything outside the model's training distribution.

### Signal 2: Stylometric Heuristics (Pure Python)

**What it measures:** Three structural statistics computed without any external API:
- **Sentence-length variance** — AI text tends toward uniform sentence lengths
- **Type-token ratio** — ratio of unique words to total words (vocabulary diversity)
- **Punctuation density** — punctuation marks per sentence

**Why it helps:** These are measurable, reproducible, and free. They catch cases where text is structurally uniform even if the semantics pass a human read.

**Output:** A score from 0.0 (human-like variability) to 1.0 (AI-like uniformity).

**What it misses:** Human writers with intentionally uniform style (technical docs, children's books, poetry with structural constraints). Very short texts (< ~20 words) where there aren't enough sentences to compute variance reliably.

### How signals are combined

```
ai_probability = (0.6 × llm_score) + (0.4 × stylometric_score)
confidence     = 1.0 - abs(llm_score - stylometric_score)
```

The LLM gets 60% weight because semantic patterns are the stronger signal for attribution. Stylometrics get 40% as a structural cross-check. Confidence reflects **agreement between signals**, not certainty about AI — a confidence of 0.5 means the signals conflict, not "50% sure it's AI."

**If deploying for real:** I'd add a third signal (perplexity scoring or a dedicated AI-detection model), calibrate thresholds on a labeled dataset rather than hand-tuning, and cache LLM results to reduce latency and cost.

---

## Confidence Scoring

### Thresholds

| ai_probability | confidence | Attribution | Label |
|---|---|---|---|
| ≥ 0.75 | ≥ 0.60 | `likely_ai` | High-confidence AI |
| ≤ 0.25 | ≥ 0.60 | `likely_human` | High-confidence human |
| anything else, OR confidence < 0.60 | — | `uncertain` | Uncertain |

The confidence gate (< 0.60 → always uncertain) is the primary false-positive safeguard. On a writing platform, labeling a human's work as AI is worse than missing AI content — so when signals disagree, the system defaults to uncertainty rather than a harsh verdict.

### Example: High-confidence human (confidence 0.94)

**Input:** Informal personal writing with typos and varied sentence lengths.

```
ok so i finally tried that new ramen place downtown and honestly? underwhelming.
the broth was fine but they put WAY too much sodium in it and i was thirsty for
like three hours after. my friend got the spicy version and said it was better.
probably wont go back unless someone drags me there
```

**Result:**

| Field | Value |
|---|---|
| attribution | `likely_human` |
| confidence | **0.94** |
| ai_probability | 0.17 |
| llm_score | 0.20 |
| stylometric_score | 0.14 |

Both signals agree the text is human-like → high confidence → high-confidence human label.

### Example: Lower-confidence case (confidence 0.41)

**Input:** Corporate AI-style paragraph with formal transitions.

```
Artificial intelligence represents a transformative paradigm shift in modern society.
It is important to note that while the benefits of AI are numerous, it is equally
essential to consider the ethical implications. Furthermore, stakeholders across
various sectors must collaborate to ensure responsible deployment.
```

**Result:**

| Field | Value |
|---|---|
| attribution | `uncertain` |
| confidence | **0.41** |
| ai_probability | 0.56 |
| llm_score | 0.80 |
| stylometric_score | 0.21 |

The LLM flags this as AI-like (0.80), but stylometrics see structural variability (0.21) — the signals disagree sharply. Confidence drops to 0.41, triggering the uncertain label even though ai_probability is above 0.5. This is the false-positive protection working as designed.

---

## Transparency Labels

Three label variants, written in plain language:

| Variant | Exact text displayed |
|---|---|
| **High-confidence AI** | "This content was likely created with AI assistance. Our analysis found consistent patterns typical of AI-generated writing." |
| **High-confidence human** | "This content appears to be original human writing. Our analysis found patterns consistent with human-authored text." |
| **Uncertain** | "We couldn't determine how this content was created with high confidence. It may be human-written, AI-assisted, or AI-generated. If you believe this is incorrect, you can submit an appeal." |

Labels change based on both `ai_probability` and `confidence` — not just the score alone. A 0.56 ai_probability with 0.41 confidence shows the uncertain label, not a soft "likely AI."

---

## Appeals Workflow

1. Creator receives a classification they disagree with.
2. Creator calls `POST /appeal` with the `content_id` from their `/submit` response and free-text `creator_reasoning`.
3. System validates the content exists and isn't already under review.
4. Content status updates from `classified` to `under_review`.
5. Appeal is logged in the audit log alongside the original classification.
6. A human reviewer (out of scope for v1) would later approve or deny the appeal.

Duplicate appeals return 409. Invalid content IDs return 404.

---

## Rate Limiting

| Limit | Value | Reasoning |
|---|---|---|
| Submissions per IP | 10 per minute | A creator typically submits 1–3 pieces when publishing. 10/min allows batch uploads while blocking automated flooding. |
| Submissions per IP | 100 per hour | Prevents sustained abuse from a single source over a longer window. |
| Appeals per IP | 5 per hour | Appeals are infrequent; 5/hour allows legitimate retries while preventing spam. |

Implementation: Flask-Limiter with in-memory storage (`storage_uri="memory://"`).

### Rate limit test evidence

Sending 12 rapid requests after 5 prior submissions in the same minute window:

```
200
200
200
200
200
429
429
429
429
429
429
429
```

The first requests within the 10/minute quota return 200; subsequent requests return 429 Too Many Requests.

---

## Audit Log

Every classification and appeal is stored as structured JSON in SQLite. Retrieve via `GET /log`.

Sample entries (from testing):

```json
{
  "entries": [
    {
      "timestamp": "2026-06-29T22:09:57.961481+00:00",
      "event_type": "classification",
      "content_id": "11c5e0e0-f37c-4a4f-ac11-2ec8e1f26a02",
      "creator_id": "test-user-1",
      "attribution": "likely_human",
      "confidence": 0.9371,
      "ai_probability": 0.1748,
      "llm_score": 0.2,
      "stylometric_score": 0.1371,
      "transparency_label": "This content appears to be original human writing. Our analysis found patterns consistent with human-authored text.",
      "status": "classified"
    },
    {
      "timestamp": "2026-06-29T22:09:58.440633+00:00",
      "event_type": "classification",
      "content_id": "71d3d62c-a69f-4d70-8eba-c8976f8995c0",
      "creator_id": "test-user-2",
      "attribution": "uncertain",
      "confidence": 0.4056,
      "ai_probability": 0.5622,
      "llm_score": 0.8,
      "stylometric_score": 0.2056,
      "transparency_label": "We couldn't determine how this content was created with high confidence. It may be human-written, AI-assisted, or AI-generated. If you believe this is incorrect, you can submit an appeal.",
      "status": "classified"
    },
    {
      "timestamp": "2026-06-29T22:10:04.918292+00:00",
      "event_type": "appeal",
      "content_id": "71d3d62c-a69f-4d70-8eba-c8976f8995c0",
      "creator_id": "test-user-2",
      "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
      "status": "under_review"
    }
  ]
}
```

---

## Known Limitations

**Repetitive poetry with simple vocabulary** — A haiku or spoken-word poem with short, uniform lines and limited vocabulary will score high on stylometric uniformity (low sentence-length variance, low type-token ratio) even when written by a human. The LLM signal may also flag the polished phrasing. Both signals can independently misread constrained poetic forms, producing an uncertain or incorrect AI classification. This is a property of the stylometric signal's reliance on variance — constrained forms reduce variance by design, not because the writer is an AI.

Other known gaps: lightly edited AI output, non-native English formal writing, and very short texts (< 20 words) where stylometrics lack enough data.

---

## Spec Reflection

**How the spec helped:** The spec's emphasis on false-positive asymmetry directly shaped my confidence gate design. The hint "a false positive is worse than a false negative on a writing platform" led me to make signal disagreement force the uncertain label rather than defaulting to whichever signal was stronger. Without that guidance, I would have used a simple weighted average with a 0.5 cutoff — which would have flagged the corporate AI paragraph as "likely AI" even when stylometrics disagreed.

**Where implementation diverged:** The planning doc expected obvious AI text to produce a `likely_ai` label with high confidence. In practice, stylometrics often score AI text as structurally human-like (because AI paragraphs can have varied sentence lengths), causing signal disagreement and routing most AI samples to `uncertain` instead. I kept this behavior intentionally — it aligns with the false-positive bias — but it means the `likely_ai` label requires both signals to strongly agree, which is rarer than I initially planned.

---

## AI Usage

### Instance 1: Generating the Flask app and detection pipeline (Milestones 3–5)

**What I directed:** Provided the detection signals section, architecture diagram, and API contract from planning.md. Asked for Flask app skeleton with `/submit`, Groq LLM signal function, stylometric heuristics, confidence scoring, and SQLite audit log.

**What it produced:** Working `app.py`, `detector.py`, `scorer.py`, and `storage.py` with route structure matching my spec.

**What I revised:** Adjusted the stylometric normalization constants after testing — the AI's default variance threshold was too aggressive and scored almost everything as human-like. I also changed the appeal request field from `reason` to `creator_reasoning` to match the milestone spec, while keeping backward compatibility. Verified the scoring thresholds (0.75/0.25/0.60) matched planning.md rather than the AI's suggested 0.5 cutoff.

### Instance 2: Writing this README (Milestone 6)

**What I directed:** Asked AI to draft README sections covering detection signals, confidence scoring with example outputs, transparency labels, audit log sample, and spec reflection — using actual test scores from Milestone 4.

**What it produced:** Structured README prose with tables and JSON examples.

**What I revised:** Rewrote the known limitations section to name a specific content type (repetitive poetry) tied to stylometric variance rather than a generic disclaimer. Adjusted the spec reflection to describe the real divergence (likely_ai label being harder to reach than planned) instead of the AI's generic "implementation went smoothly" framing.


## Architecture

Submission flow: a platform POSTs text to `/submit`, which passes rate limiting, runs both detection signals, combines them into a confidence score, selects a transparency label, writes a structured audit log entry, and returns the full result. Appeal flow: a creator POSTs a `content_id` and reasoning to `/appeal`, which updates the content status to `under_review` and appends an appeal event to the audit log.

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
