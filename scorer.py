LABEL_LIKELY_AI = (
    "This content was likely created with AI assistance. "
    "Our analysis found consistent patterns typical of AI-generated writing."
)

LABEL_LIKELY_HUMAN = (
    "This content appears to be original human writing. "
    "Our analysis found patterns consistent with human-authored text."
)

LABEL_UNCERTAIN = (
    "We couldn't determine how this content was created with high confidence. "
    "It may be human-written, AI-assisted, or AI-generated. "
    "If you believe this is incorrect, you can submit an appeal."
)

AI_THRESHOLD = 0.75
HUMAN_THRESHOLD = 0.25
CONFIDENCE_GATE = 0.60

LLM_WEIGHT = 0.6
STYLO_WEIGHT = 0.4


def score_content(llm_score: float, stylometric_score: float) -> dict:
    ai_probability = round(LLM_WEIGHT * llm_score + STYLO_WEIGHT * stylometric_score, 4)
    confidence = round(1.0 - abs(llm_score - stylometric_score), 4)

    if confidence < CONFIDENCE_GATE:
        attribution = "uncertain"
        label = LABEL_UNCERTAIN
    elif ai_probability >= AI_THRESHOLD:
        attribution = "likely_ai"
        label = LABEL_LIKELY_AI
    elif ai_probability <= HUMAN_THRESHOLD:
        attribution = "likely_human"
        label = LABEL_LIKELY_HUMAN
    else:
        attribution = "uncertain"
        label = LABEL_UNCERTAIN

    return {
        "attribution": attribution,
        "confidence": confidence,
        "ai_probability": ai_probability,
        "transparency_label": label,
    }
