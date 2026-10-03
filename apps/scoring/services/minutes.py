"""Minutes from the exact score population, frozen alongside its publication."""
from collections import defaultdict


def competition_minutes(rows):
    totals = defaultdict(int)
    for row in rows:
        competition = row.fixture.competition_season.competition
        totals[(competition.pk, competition.name, competition.participant_type)] += row.minutes
    return [
        {"competition_id": pk, "competition": name, "participant_type": participants, "minutes": minutes}
        for (pk, name, participants), minutes in sorted(totals.items(), key=lambda item: (item[0][1], item[0][0]))
        if minutes
    ]


def displayed_minutes(summary, total):
    """Read only frozen subtotals; never reconstruct old publications from live rows."""
    groups = []
    detail = summary.get("competition_minutes")
    if detail is not None:
        for kind, label in (("CLUB", "Club"), ("NATIONAL", "National team"), ("UNKNOWN", "Other covered competitions")):
            rows = [row for row in detail if row["participant_type"] == kind and row["minutes"]]
            if rows:
                groups.append({"label": label, "rows": rows})
        return groups
    rows = [
        {"competition": label, "minutes": summary.get(key, 0)}
        for key, label in (("domestic_minutes", "Domestic leagues"), ("ucl_minutes", "Champions League"))
        if summary.get(key, 0)
    ]
    known = sum(row["minutes"] for row in rows)
    if known > total:
        return [{"label": "Historical coverage", "rows": [{"competition": "Covered competitions (detail not retained)", "minutes": total}]}]
    if rows:
        groups.append({"label": "Club", "rows": rows})
    if total > known:
        groups.append({"label": "Historical coverage", "rows": [
            {"competition": "Other covered competitions", "minutes": total - known}]})
    return groups
