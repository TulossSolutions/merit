from django.templatetags.static import static


DEFAULT_META_DESCRIPTION = (
    "Compare football players with Merit's transparent weekly rankings. "
    "Explore season performance, positional scores and the methodology. No votes. Just performance."
)


def metadata(request):
    image_url = request.build_absolute_uri(static("images/merit_logo_full.png"))
    return {
        "default_meta_description": DEFAULT_META_DESCRIPTION,
        "social_image": {
            "url": image_url,
            "secure": image_url.startswith("https://"),
            "type": "image/png",
            "width": 1200,
            "height": 800,
            "alt": "Merit logo with a black wordmark and black-and-gold emblem",
        },
    }
