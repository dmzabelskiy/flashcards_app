(function () {
  var card = document.querySelector("[data-flashcard]");
  var form = document.querySelector("[data-flashcard-form]");
  if (!card || !form) return;

  var front = card.querySelector('[data-flashcard-face="front"]');
  var back = card.querySelector('[data-flashcard-face="back"]');
  var backButton = form.querySelector("[data-flashcard-back]");
  var meta = document.querySelector(".flashcard-meta");
  var flipDuration = 650;
  var reverseFlipDuration = 350;
  var halfFlipDuration = 325;
  var busy = false;
  var fallbackScheduled = false;
  var nextFallbackScheduled = false;
  var flipStartedAt = 0;
  var nextFlipStartedAt = 0;
  var lastClickedButton = null;

  function setInert(face, inert) {
    if (!face) return;
    face.setAttribute("aria-hidden", inert ? "true" : "false");
    if (inert) {
      face.setAttribute("inert", "");
    } else {
      face.removeAttribute("inert");
    }
    face.inert = inert;
  }

  function setFaceState(revealed) {
    setInert(front, revealed);
    setInert(back, !revealed);
    card.dataset.revealed = revealed ? "true" : "false";
    setBackButtonState(busy);
  }

  function setButtonBusy(button, isBusy) {
    button.disabled = isBusy;
    if (isBusy) {
      button.setAttribute("aria-busy", "true");
    } else {
      button.removeAttribute("aria-busy");
    }
  }

  function setBackButtonState(isBusy) {
    if (!backButton) return;
    var available = card.dataset.backAvailable === "true";
    var disabled = isBusy || !available;
    backButton.disabled = disabled;
    if (disabled) {
      backButton.setAttribute("aria-disabled", "true");
    } else {
      backButton.removeAttribute("aria-disabled");
    }
    if (isBusy) {
      backButton.setAttribute("aria-busy", "true");
    } else {
      backButton.removeAttribute("aria-busy");
    }
  }

  function setNextButton(button) {
    button.name = "action";
    button.value = "next";
    setButtonBusy(button, false);
    button.textContent = "Next card ";
    var arrow = document.createElement("span");
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = "→";
    button.appendChild(arrow);
    setBackButtonState(false);
  }

  function setShowAnswerButton(button, isBusy) {
    button.name = "action";
    button.value = "show_answer";
    setButtonBusy(button, isBusy);
    button.textContent = "Show answer";
    setBackButtonState(isBusy);
  }

  function nativeSubmit(actionName) {
    var actionButtons = form.querySelectorAll('button[name="action"]');
    for (var index = 0; index < actionButtons.length; index += 1) {
      actionButtons[index].disabled = true;
    }
    var action = form.querySelector("[data-flashcard-fallback-action]");
    if (!action) {
      action = document.createElement("input");
      action.type = "hidden";
      action.name = "action";
      action.dataset.flashcardFallbackAction = "";
      form.appendChild(action);
    }
    action.value = actionName;
    action.dataset.flashcardFallbackAction = actionName;
    HTMLFormElement.prototype.submit.call(form);
  }

  function nativeRevealSubmit() {
    nativeSubmit("show_answer");
  }

  function nativeNextSubmit() {
    nativeSubmit("next");
  }

  function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function scheduleNativeFallback() {
    if (fallbackScheduled) return;
    fallbackScheduled = true;
    var elapsed = flipStartedAt ? Date.now() - flipStartedAt : 0;
    var remaining = prefersReducedMotion() ? 0 : Math.max(0, flipDuration - elapsed);
    window.setTimeout(nativeRevealSubmit, remaining);
  }

  function scheduleNativeNextFallback() {
    if (nextFallbackScheduled) return;
    nextFallbackScheduled = true;
    var elapsed = nextFlipStartedAt ? Date.now() - nextFlipStartedAt : 0;
    var remaining = prefersReducedMotion() ? 0 : Math.max(0, flipDuration - elapsed);
    window.setTimeout(function () {
      setFaceState(false);
      nativeNextSubmit();
    }, remaining);
  }

  function requestFrame(callback) {
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(callback);
    } else {
      window.setTimeout(callback, 0);
    }
  }

  function startReverseTransition(onEdge) {
    // Adding the midpoint state while the card is still revealed lets the
    // next frame animate from 180° to 90° instead of taking the short path
    // directly to the front face.
    card.classList.add("is-next-edge");
    if (prefersReducedMotion()) {
      card.classList.remove("is-revealed");
      onEdge();
      return;
    }

    requestFrame(function () {
      card.classList.remove("is-revealed");
      window.setTimeout(onEdge, halfFlipDuration);
    });
  }

  function startReverseFlip(button) {
    if (card.dataset.revealed !== "true") return;

    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setBackButtonState(true);
    // Keep the compact reverse transition as the fallback when fetch-based
    // hydration is unavailable; capable browsers use the staged preload.
    card.classList.add("is-returning");
    card.getBoundingClientRect();
    card.classList.remove("is-revealed");
    setFaceState(false);

    var delay = prefersReducedMotion() ? 0 : reverseFlipDuration;
    window.setTimeout(nativeNextSubmit, delay);
  }

  function finishReverseTransition(onFront) {
    card.classList.add("is-next-settling");
    if (prefersReducedMotion()) {
      card.classList.remove("is-next-edge", "is-next-settling");
      onFront();
      return;
    }
    requestFrame(function () {
      card.classList.remove("is-next-edge");
      window.setTimeout(function () {
        card.classList.remove("is-next-settling");
        onFront();
      }, halfFlipDuration);
    });
  }

  function safeReload() {
    if (window.location && typeof window.location.reload === "function") {
      window.location.reload();
    } else if (window.location && typeof window.location.assign === "function") {
      window.location.assign(window.location.href);
    }
  }

  function parseNextResponse(response, html, endpoint) {
    var destination = new URL(response.url || endpoint, window.location.href);
    var parsed = new DOMParser().parseFromString(html, "text/html");
    var incomingCard = parsed.querySelector("[data-flashcard]");
    if (incomingCard) {
      var incomingFront = incomingCard.querySelector('[data-flashcard-face="front"]');
      var incomingBack = incomingCard.querySelector('[data-flashcard-face="back"]');
      var incomingMeta = parsed.querySelector(".flashcard-meta");
      if (!incomingFront || !incomingBack || !incomingMeta) {
        throw new Error("Flashcard response is missing its card faces or progress");
      }
      return {
        kind: "card",
        front: incomingFront,
        back: incomingBack,
        meta: incomingMeta,
        backAvailable: incomingCard.dataset.backAvailable === "true",
      };
    }

    if (parsed.querySelector(".flashcard-complete-panel")) {
      return { kind: "complete", url: destination.href };
    }

    throw new Error("Flashcard response did not contain a card or completion page");
  }

  function hydrateNextCard(nextCard, button) {
    front.innerHTML = nextCard.front.innerHTML;
    back.innerHTML = nextCard.back.innerHTML;
    meta.innerHTML = nextCard.meta.innerHTML;
    card.dataset.backAvailable = nextCard.backAvailable ? "true" : "false";
    setShowAnswerButton(button, true);
    setFaceState(false);
  }

  function preloadNextCard(button) {
    var endpoint = form.getAttribute("action") || window.location.href;
    var body;
    var request;
    try {
      body = new FormData(form);
      body.set("action", "next");
      request = window.fetch(endpoint, {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
    } catch (error) {
      startReverseTransition(function () {
        setFaceState(false);
        scheduleNativeNextFallback();
      });
      return;
    }

    var nextResult = null;
    var nextError = null;
    var edgeReached = false;
    var settledAtEdge = false;

    function settleAtEdge() {
      if (!edgeReached || settledAtEdge) return;
      if (!nextResult && !nextError) return;
      settledAtEdge = true;
      if (nextError) {
        // The POST may have reached the server even when its response failed.
        // Reloading the current URL observes that authoritative session state
        // without risking a second next-card POST.
        safeReload();
        return;
      }
      if (nextResult.kind === "complete") {
        // There is no next face to hydrate. Complete the return half with
        // both faces hidden, then let the server-rendered completion page
        // take over without briefly exposing the old question.
        card.classList.add("is-next-complete");
        finishReverseTransition(function () {
          window.location.assign(nextResult.url);
        });
        return;
      }
      hydrateNextCard(nextResult, button);
      finishReverseTransition(function () {
        busy = false;
        setShowAnswerButton(button, false);
        button.focus();
      });
    }

    Promise.resolve(request)
      .then(function (response) {
        if (response.ok === false || !response.text) {
          throw new Error("Flashcard next request failed");
        }
        return response.text().then(function (html) {
          return parseNextResponse(response, html, endpoint);
        });
      })
      .then(function (result) {
        nextResult = result;
        settleAtEdge();
      })
      .catch(function (error) {
        nextError = error;
        settleAtEdge();
      });

    // The request starts before this visual transition, so the server can
    // render the next card while the current answer rotates out of view.
    startReverseTransition(function () {
      edgeReached = true;
      settleAtEdge();
    });
  }

  setFaceState(card.dataset.revealed === "true");

  // Older browsers do not expose submitter on the submit event. Remembering
  // the clicked control keeps the Back-first layout progressive there too.
  form.addEventListener("click", function (event) {
    var target = event.target;
    while (target && target !== form && target.tagName !== "BUTTON") {
      target = target.parentNode;
    }
    if (target && target.tagName === "BUTTON" && target.type === "submit") {
      lastClickedButton = target;
    }
  });

  form.addEventListener("submit", function (event) {
    var button = event.submitter || lastClickedButton || form.querySelector('button[type="submit"]');
    lastClickedButton = null;
    if (!button) return;

    if (button.value === "next") {
      // Keep duplicate events from falling through to the browser's native
      // submit after the reverse transition has already started.
      if (busy) {
        event.preventDefault();
        return;
      }
      if (card.dataset.revealed !== "true") return;

      event.preventDefault();
      busy = true;
      nextFlipStartedAt = Date.now();
      setButtonBusy(button, true);
      setBackButtonState(true);

      if (!window.fetch || !window.FormData || !window.DOMParser) {
        startReverseFlip(button);
        return;
      }

      preloadNextCard(button);
      return;
    }

    if (button.value !== "show_answer") return;

    event.preventDefault();
    if (busy || card.dataset.revealed === "true") return;

    if (!window.fetch || !window.FormData) {
      nativeRevealSubmit();
      return;
    }

    busy = true;
    flipStartedAt = Date.now();
    button.disabled = true;
    button.setAttribute("aria-busy", "true");

    // Flip immediately so the interaction is visible while the server keeps
    // the session authoritative. The POST is idempotent if a fallback retries.
    card.classList.add("is-revealed");
    setFaceState(true);

    var body = new FormData(form);
    body.set("action", "show_answer");
    var endpoint = form.getAttribute("action") || window.location.href;

    window.fetch(endpoint, {
      method: "POST",
      body: body,
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (response) {
        var destination = new URL(response.url, window.location.href);
        return response.text().then(function (html) {
          var renderedRevealed = /name=["']action["'][^>]*value=["']next["']/.test(html);
          if (!response.ok || destination.pathname.indexOf("/accounts/login/") !== -1 || !renderedRevealed) {
            throw new Error("Flashcard reveal did not return a revealed session state");
          }
        });
      })
      .then(function () {
        busy = false;
        setNextButton(button);
        button.focus();
      })
      .catch(function () {
        // Keep the visible flip intact until its 650ms transition completes.
        // The native submit then redirects to the revealed page and preserves
        // the POST. Reduced motion falls back immediately.
        scheduleNativeFallback();
      });
  });
})();
