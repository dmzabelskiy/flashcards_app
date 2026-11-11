"""The three views every drill is served through, resolved from the registry.

``reviews/drill/<slug>/start/``, ``.../`` and ``.../complete/`` look the drill
up in the active study mode's pack.  A slug the mode does not offer sends the
learner back to the drills index; a slug no pack has is a 404.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect

from core.language_modes import get_language_mode
from reviews import drill_engine
from reviews.drill_specs import spec_for
from reviews.drills import drill_exists, drill_for_slug


def _resolve(request, slug):
    """Return (spec, None), or (None, redirect) for a drill the mode does not offer."""
    mode = get_language_mode(request)
    drill = drill_for_slug(mode, slug)
    if drill is not None:
        return spec_for(drill), None
    if not drill_exists(slug):
        raise Http404("No such drill")
    messages.info(request, "That drill is not available in the selected language mode.")
    return None, redirect("reviews:drills")


def drill_start(request, slug):
    spec, response = _resolve(request, slug)
    return response or drill_engine.start_round(request, spec)


def drill_step(request, slug):
    spec, response = _resolve(request, slug)
    return response or drill_engine.step_round(request, spec)


def drill_complete(request, slug):
    spec, response = _resolve(request, slug)
    return response or drill_engine.complete_round(request, spec)
