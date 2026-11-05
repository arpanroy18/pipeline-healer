def process_user_metrics(scores: list[int]) -> dict:
    """Calculate the total and average score."""
    if not scores:
        return {"total": 0, "average": 0.0}

    total_score = sum(scores)
    average_score = total_score / len(scores)

    return {
        "total": total_score,
        "average": average_score
    }
