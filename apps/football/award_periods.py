from django.core.exceptions import ValidationError
from .models import Season


def award_year(played_on):
    """June/July tournaments close the preceding season, not the following one."""
    return played_on.year if played_on.month>=8 else played_on.year-1


def ensure_award_period(year):
    from datetime import date
    return Season.objects.get_or_create(slug=f"{year}-{str(year+1)[-2:]}",defaults={
        "name":f"{year}/{str(year+1)[-2:]}","starts_on":date(year,8,1),"ends_on":date(year+1,5,31)})[0]


def fixtures_for_award_period(season):
    from django.db.models import Q
    from .models import Fixture
    return Fixture.objects.filter(Q(award_season=season)|Q(award_season__isnull=True,competition_season__season=season))


def summer_award_period(starts_on, ends_on):
    """Summer national-team editions belong to the most recently ended award period."""
    if not starts_on or not ends_on or ends_on < starts_on:
        raise ValidationError("Valid edition dates are required for summer assignment.")
    season=Season.objects.filter(ends_on__lte=starts_on).order_by("-ends_on").first()
    if not season or (starts_on-season.ends_on).days>120:
        raise ValidationError("No recently ended award period exists; create it before assigning the tournament.")
    return season
