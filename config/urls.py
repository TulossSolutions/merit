from django.contrib import admin
from django.urls import path
from django.contrib.sitemaps.views import sitemap
from apps.core.sitemaps import PlayerSitemap,SeasonSitemap,StaticSitemap
from apps.core import views as core
from apps.rankings import views as rankings

urlpatterns=[
    path("",core.home,name="home"),
    path("healthz/",core.health,name="health"),
    path("robots.txt",core.robots,name="robots"),
    path("sitemap.xml",sitemap,{"sitemaps":{"static":StaticSitemap,"players":PlayerSitemap,"seasons":SeasonSitemap}},name="sitemap"),
    path("rankings/",rankings.ranking_index,name="ranking_index"),
    path("rankings/<slug:position_slug>/",rankings.ranking,name="ranking"),
    path("players/search/",rankings.player_search,name="player_search"),
    path("players/<slug:slug>/",rankings.player_detail,name="player_detail"),
    path("compare/",rankings.compare,name="compare"),
    path("methodology/",core.methodology,name="methodology"),
    path("roadmap/",core.roadmap,name="roadmap"),
    path("manifesto/",core.manifesto,name="manifesto"),
    path("methodology/changelog/",core.changelog,name="changelog"),
    path("seasons/<slug:slug>/",core.season_archive,name="season_archive"),
    path("admin/",admin.site.urls),
]
