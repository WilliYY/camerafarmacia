from collections import defaultdict
from datetime import datetime
from itertools import islice


DEFAULT_CONFIRMATION_GAP_SECONDS = 180
MAX_ACTIVITY_INPUTS = 2000
MAX_ACTIVITY_ROWS = 500


def _parse_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _confidence(value):
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError, OverflowError):
        return None


def _profile_metadata(profiles):
    result = {}
    for item in profiles or []:
        profile_id = str(item.get("profile_id") or "").strip()[:80]
        if not profile_id:
            continue
        result[profile_id] = {
            "display_name": str(item.get("display_name") or "Perfil local").strip()[:80]
            or "Perfil local",
            "role": str(item.get("role") or "authorized")[:40],
        }
    return result


def _activity(profile_id, metadata, kind, occurred_at, description, **values):
    profile = metadata[profile_id]
    return {
        "profile_id": profile_id,
        "display_name": profile["display_name"],
        "role": profile["role"],
        "kind": kind,
        "occurred_at": occurred_at.isoformat(timespec="seconds"),
        "description": description,
        **values,
    }


def build_profile_activity(
    observations,
    profiles,
    now=None,
    confirmation_gap_seconds=DEFAULT_CONFIRMATION_GAP_SECONDS,
    limit=200,
):
    confirmation_gap_seconds = max(
        60.0, min(float(confirmation_gap_seconds), 3600.0)
    )
    limit = max(1, min(int(limit), MAX_ACTIVITY_ROWS))
    metadata = _profile_metadata(profiles)
    grouped = defaultdict(list)

    for item in islice(observations or (), MAX_ACTIVITY_INPUTS):
        if item.get("event_type") != "presence_confirmed":
            continue
        profile_id = str(item.get("profile_id") or "").strip()[:80]
        stream = str(item.get("stream") or "").strip()[:80]
        occurred_at = _parse_datetime(item.get("occurred_at"))
        if (
            not profile_id
            or profile_id not in metadata
            or not stream
            or occurred_at is None
        ):
            continue
        grouped[profile_id].append(
            {
                "occurred_at": occurred_at,
                "stream": stream,
                "confidence": _confidence(item.get("confidence")),
            }
        )

    activities = []
    for profile_id, profile_observations in grouped.items():
        profile_observations.sort(
            key=lambda item: (item["occurred_at"], item["stream"])
        )
        segments = []
        for observation in profile_observations:
            if not segments:
                segments.append(
                    {
                        "stream": observation["stream"],
                        "first_at": observation["occurred_at"],
                        "last_at": observation["occurred_at"],
                        "confidences": [observation["confidence"]],
                        "confirmation_count": 1,
                    }
                )
                continue
            previous = segments[-1]
            elapsed = (observation["occurred_at"] - previous["last_at"]).total_seconds()
            if (
                previous["stream"] == observation["stream"]
                and 0.0 <= elapsed <= confirmation_gap_seconds
            ):
                previous["last_at"] = observation["occurred_at"]
                previous["confidences"].append(observation["confidence"])
                previous["confirmation_count"] += 1
            else:
                segments.append(
                    {
                        "stream": observation["stream"],
                        "first_at": observation["occurred_at"],
                        "last_at": observation["occurred_at"],
                        "confidences": [observation["confidence"]],
                        "confirmation_count": 1,
                    }
                )

        for index, segment in enumerate(segments):
            confidences = [
                value for value in segment["confidences"] if value is not None
            ]
            duration = max(
                0.0,
                (segment["last_at"] - segment["first_at"]).total_seconds(),
            )
            repeated = segment["confirmation_count"] > 1
            activities.append(
                _activity(
                    profile_id,
                    metadata,
                    "observed_window" if repeated else "observation",
                    segment["last_at"],
                    (
                        f"Confirmações repetidas em {segment['stream'].upper()}"
                        if repeated
                        else f"Confirmado em {segment['stream'].upper()}"
                    ),
                    from_stream=None,
                    to_stream=segment["stream"],
                    duration_seconds=duration,
                    confirmation_count=segment["confirmation_count"],
                    confidence=(sum(confidences) / len(confidences))
                    if confidences
                    else None,
                    current=False,
                )
            )
            if index == 0:
                continue
            previous = segments[index - 1]
            elapsed = max(
                0.0,
                (segment["first_at"] - previous["last_at"]).total_seconds(),
            )
            if elapsed <= confirmation_gap_seconds:
                simultaneous = elapsed == 0.0
                activities.append(
                    _activity(
                        profile_id,
                        metadata,
                        "transition",
                        segment["first_at"],
                        (
                            "Confirmações simultâneas: "
                            f"{previous['stream'].upper()} e {segment['stream'].upper()}"
                            if simultaneous
                            else "Sequência observada: "
                            f"{previous['stream'].upper()} → {segment['stream'].upper()}"
                        ),
                        from_stream=previous["stream"],
                        to_stream=segment["stream"],
                        duration_seconds=elapsed,
                        confirmation_count=2,
                        confidence=None,
                        current=False,
                        simultaneous=simultaneous,
                    )
                )
            else:
                activities.append(
                    _activity(
                        profile_id,
                        metadata,
                        "coverage_gap",
                        segment["first_at"],
                        (
                            "Intervalo sem confirmação visual entre "
                            f"{previous['stream'].upper()} e {segment['stream'].upper()}"
                        ),
                        from_stream=previous["stream"],
                        to_stream=segment["stream"],
                        duration_seconds=elapsed,
                        confirmation_count=0,
                        confidence=None,
                        current=False,
                    )
                )

        latest = segments[-1]
        current_time = _parse_datetime(now) or datetime.now().replace(microsecond=0)
        current_elapsed = (current_time - latest["last_at"]).total_seconds()
        if current_elapsed > confirmation_gap_seconds:
            activities.append(
                _activity(
                    profile_id,
                    metadata,
                    "coverage_gap",
                    latest["last_at"],
                    (
                        "Sem nova confirmação visual após "
                        f"{latest['stream'].upper()}"
                    ),
                    from_stream=latest["stream"],
                    to_stream=None,
                    duration_seconds=current_elapsed,
                    confirmation_count=0,
                    confidence=None,
                    current=True,
                    evaluated_at=current_time.isoformat(timespec="seconds"),
                )
            )

    activities.sort(
        key=lambda item: (
            item["occurred_at"],
            item["kind"] == "coverage_gap",
            item["display_name"].casefold(),
        ),
        reverse=True,
    )
    transition_count = sum(item["kind"] == "transition" for item in activities)
    coverage_gap_count = sum(
        item["kind"] == "coverage_gap" for item in activities
    )
    activities = activities[:limit]
    return {
        "activities": activities,
        "summary": {
            "profile_count": len(grouped),
            "observation_count": sum(len(items) for items in grouped.values()),
            "transition_count": transition_count,
            "coverage_gap_count": coverage_gap_count,
        },
    }
