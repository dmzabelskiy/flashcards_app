(function () {
  "use strict";

  const app = document.getElementById("video-study-app");
  const transcriptData = document.getElementById("video-transcript-data");
  const transcriptList = document.getElementById("transcript-list");
  if (!app || !transcriptData || !transcriptList) {
    return;
  }

  const videoId = app.dataset.videoId;
  const sourceLanguage = app.dataset.sourceLanguage || "de";
  const targetLanguage = app.dataset.targetLanguage || "ru";
  const targetLanguageLabel = app.dataset.targetLanguageLabel || "Russian";
  const translateUrl = app.dataset.translateUrl;
  const addToGlobalUrl = app.dataset.addToGlobalUrl;
  const sourceUrl = app.dataset.sourceUrl || "";
  const csrfInput = app.querySelector('input[name="csrfmiddlewaretoken"]');
  const csrfToken = csrfInput ? csrfInput.value : "";
  const playerStatus = document.getElementById("player-status");
  const translationPanel = document.getElementById("translation-panel");
  const translationSource = document.getElementById("translation-source");
  const translationResult = document.getElementById("translation-result");
  const translationContext = document.getElementById("translation-context");
  const translationMetadata = document.getElementById("translation-metadata");
  const translationGrammar = document.getElementById("translation-grammar");
  const translationGender = document.getElementById("translation-gender");
  const translationPlural = document.getElementById("translation-plural");
  const translationDictionaryStatus = document.getElementById("translation-dictionary-status");
  const addToGlobal = document.getElementById("add-to-global");
  const globalDeckStatus = document.getElementById("global-deck-status");
  const translationClose = document.getElementById("translation-close");
  const theaterToggle = document.getElementById("theater-toggle");
  const theaterLabel = document.getElementById("theater-label");
  const wordPattern = /[\p{L}\p{M}]+(?:['’\-][\p{L}\p{M}]+)*/gu;
  const rawCues = JSON.parse(transcriptData.textContent);
  const bilingual = app.dataset.bilingual === "true";
  const captionTiming = window.VideoCaptions;
  const englishData = document.getElementById("video-english-transcript-data");
  const englishCues = bilingual && englishData
    ? captionTiming.timedCues(JSON.parse(englishData.textContent)) : [];
  const dutchCaptionCues = bilingual ? captionTiming.timedCues(rawCues) : [];
  const currentDutchCaption = document.getElementById("current-dutch-caption");
  const currentEnglishCaption = document.getElementById("current-english-caption");
  const atomicCues = rawCues.map(function (cue, index) {
    const nextStart = rawCues[index + 1] ? Number(rawCues[index + 1].start) : Infinity;
    const naturalEnd = Number(cue.start) + Math.max(Number(cue.duration), 0.2);
    return {
      text: String(cue.text),
      start: Number(cue.start),
      end: Math.min(naturalEnd, nextStart > Number(cue.start) ? nextStart : naturalEnd),
      element: null,
    };
  });

  const MAX_SENTENCE_WORDS = 28;
  const MAX_SENTENCE_CHARACTERS = 180;
  const MAX_SENTENCE_PAUSE = 1.5;

  function endsSentence(text) {
    return /[.!?…]+(?:["\x27»”’)\]]*)$/.test(text.trim());
  }

  function wordCount(text) {
    const words = text.match(wordPattern);
    return words ? words.length : 0;
  }

  function groupCues(cueItems) {
    const grouped = [];
    let current = null;

    cueItems.forEach(function (cue) {
      if (!current) {
        current = { ...cue };
        return;
      }

      const pause = Math.max(0, cue.start - current.end);
      const nextText = current.text + " " + cue.text;
      const shouldStartNewSentence = endsSentence(current.text) ||
        (pause > MAX_SENTENCE_PAUSE && wordCount(current.text) >= 4) ||
        wordCount(nextText) > MAX_SENTENCE_WORDS ||
        nextText.length > MAX_SENTENCE_CHARACTERS;

      if (shouldStartNewSentence) {
        grouped.push(current);
        current = { ...cue };
        return;
      }

      current.text = nextText;
      current.end = Math.max(current.end, cue.end);
    });

    if (current) {
      grouped.push(current);
    }
    return grouped;
  }

  const cues = groupCues(atomicCues);

  let player = null;
  let activeCueIndex = -1;
  let selectedWord = null;
  let selectedWordData = null;
  let syncTimer = null;
  let translationRequestVersion = 0;
  let translationPrefetchTimer = null;
  const translationCache = new Map();
  const translationRequests = new Map();

  function formatTime(seconds) {
    const rounded = Math.max(0, Math.floor(seconds));
    const minutes = Math.floor(rounded / 60);
    const remainder = String(rounded % 60).padStart(2, "0");
    return minutes + ":" + remainder;
  }

  function appendTokenizedText(container, text, cue) {
    let cursor = 0;
    for (const match of text.matchAll(wordPattern)) {
      if (match.index > cursor) {
        container.appendChild(document.createTextNode(text.slice(cursor, match.index)));
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "transcript-word";
      button.dataset.word = match[0];
      button.dataset.context = cue.text;
      button.textContent = match[0];
      button.title = 'Translate "' + match[0] + '" into ' + targetLanguageLabel;
      container.appendChild(button);
      cursor = match.index + match[0].length;
    }
    if (cursor < text.length) {
      container.appendChild(document.createTextNode(text.slice(cursor)));
    }
  }

  function renderTranscript() {
    const fragment = document.createDocumentFragment();
    cues.forEach(function (cue, index) {
      const row = document.createElement("p");
      row.className = "transcript-cue";
      row.dataset.index = String(index);
      row.dataset.start = String(cue.start);

      const timestamp = document.createElement("button");
      timestamp.type = "button";
      timestamp.className = "transcript-time";
      timestamp.dataset.seek = String(cue.start);
      timestamp.textContent = formatTime(cue.start);
      timestamp.setAttribute("aria-label", "Seek video to " + formatTime(cue.start));

      const text = document.createElement("span");
      text.className = "transcript-text";
      text.lang = sourceLanguage;
      appendTokenizedText(text, cue.text, cue);

      row.append(timestamp, text);
      if (bilingual) {
        const references = captionTiming.overlappingCues(englishCues, cue.start, cue.end);
        if (references.length) {
          const reference = document.createElement("span");
          reference.className = "transcript-reference";
          reference.lang = "en";
          reference.setAttribute("aria-label", "English reference");
          captionTiming.setReferenceText(reference,
            references.map(function (item) { return item.text; }).join(" "));
          row.appendChild(reference);
        }
      }
      cue.element = row;
      fragment.appendChild(row);
    });
    transcriptList.appendChild(fragment);
  }

  function setActiveCue(index) {
    if (index === activeCueIndex) {
      return;
    }
    if (activeCueIndex >= 0 && cues[activeCueIndex]) {
      cues[activeCueIndex].element.classList.remove("is-active");
    }
    activeCueIndex = index;
    if (index >= 0 && cues[index]) {
      const element = cues[index].element;
      element.classList.add("is-active");
      centerCue(element, "smooth");
    }
  }

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function centerCue(element, behavior) {
    if (!element || transcriptList.scrollHeight <= transcriptList.clientHeight) {
      return;
    }
    const listRectangle = transcriptList.getBoundingClientRect();
    const cueRectangle = element.getBoundingClientRect();
    const cueTopInList = transcriptList.scrollTop + cueRectangle.top - listRectangle.top;
    const centeredTop = cueTopInList -
      Math.max(0, (transcriptList.clientHeight - cueRectangle.height) / 2);
    transcriptList.scrollTo({
      top: Math.max(0, centeredTop),
      behavior: reducedMotion() ? "auto" : behavior,
    });
  }

  function centerActiveCue(behavior) {
    if (activeCueIndex < 0 || !cues[activeCueIndex]) {
      return;
    }
    centerCue(cues[activeCueIndex].element, behavior || "smooth");
  }

  function centerActiveCueAfterLayout(behavior) {
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        centerActiveCue(behavior);
      });
    });
  }

  function syncTranscript() {
    if (!player || typeof player.getCurrentTime !== "function") {
      return;
    }
    const currentTime = player.getCurrentTime();
    if (bilingual) {
      captionTiming.setReferenceText(currentDutchCaption,
        captionTiming.currentText(dutchCaptionCues, currentTime));
      captionTiming.setReferenceText(currentEnglishCaption,
        captionTiming.currentText(englishCues, currentTime));
    }
    let match = -1;
    for (let index = 0; index < cues.length; index += 1) {
      if (currentTime >= cues[index].start && currentTime < cues[index].end) {
        match = index;
        break;
      }
      if (cues[index].start > currentTime) {
        break;
      }
    }
    setActiveCue(match);
  }

  function startSync() {
    if (!syncTimer) {
      syncTimer = window.setInterval(syncTranscript, 250);
    }
  }

  function stopSync() {
    if (syncTimer) {
      window.clearInterval(syncTimer);
      syncTimer = null;
    }
  }

  window.onYouTubeIframeAPIReady = function () {
    player = new window.YT.Player("youtube-player", {
      videoId: videoId,
      playerVars: {
        playsinline: 1,
        rel: 0,
      },
      events: {
        onReady: function () {
          playerStatus.textContent = "Ready";
          syncTranscript();
          if (bilingual) {
            // Keep observing while paused: seeking in YouTube's controls may
            // change the time without another player-state event.
            startSync();
          }
        },
        onStateChange: function (event) {
          if (event.data === window.YT.PlayerState.PLAYING) {
            playerStatus.textContent = "Playing";
            startSync();
          } else {
            playerStatus.textContent =
              event.data === window.YT.PlayerState.PAUSED ? "Paused" : "Ready";
            if (!bilingual) {
              stopSync();
            }
            syncTranscript();
          }
        },
        onError: function () {
          playerStatus.textContent = "Player unavailable";
        },
      },
    });
  };

  function loadYouTubeApi() {
    if (window.YT && window.YT.Player) {
      window.onYouTubeIframeAPIReady();
      return;
    }
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.async = true;
    document.head.appendChild(script);
  }

  function clearSelectedWord() {
    if (selectedWord) {
      selectedWord.classList.remove("is-selected");
      selectedWord = null;
    }
    selectedWordData = null;
    addToGlobal.hidden = true;
    addToGlobal.disabled = false;
    addToGlobal.textContent = "Add to global deck";
    translationMetadata.hidden = true;
    globalDeckStatus.textContent = "";
    if (translationDictionaryStatus) {
      translationDictionaryStatus.hidden = true;
    }
  }

  function renderSuggestion(suggestion) {
    const panel = window.CapturePanel.describe(suggestion);
    translationGrammar.textContent = panel.grammar;
    translationGender.textContent = panel.gender;
    translationPlural.textContent = panel.plural;
    translationContext.textContent = panel.example;
    if (translationDictionaryStatus) {
      translationDictionaryStatus.textContent = panel.status;
      translationDictionaryStatus.hidden = !panel.status;
    }
    translationMetadata.hidden = !selectedWord;
  }

  function translationKey(text, context) {
    return [sourceLanguage, targetLanguage, text, context || text]
      .map(function (value) {
        return String(value).replace(/\s+/g, " ").trim().toLocaleLowerCase();
      })
      .join("\u001f");
  }

  function translationUsesTargetScript(result) {
    const translated = result && String(result.translation || "").trim();
    if (!translated) {
      return false;
    }
    return targetLanguage !== "ru" || /[\u0400-\u04ff]/.test(translated);
  }

  function readSessionTranslation(key) {
    if (translationCache.has(key)) {
      const result = translationCache.get(key);
      if (translationUsesTargetScript(result)) {
        return result;
      }
      translationCache.delete(key);
    }
    try {
      const storageKey = "video-translation:" + key;
      const stored = window.sessionStorage.getItem(storageKey);
      if (stored) {
        const result = JSON.parse(stored);
        if (translationUsesTargetScript(result)) {
          translationCache.set(key, result);
          return result;
        }
        window.sessionStorage.removeItem(storageKey);
      }
    } catch (_error) {
      // Storage can be unavailable in strict privacy modes; the in-memory
      // cache still prevents duplicate requests for the lifetime of the page.
    }
    return null;
  }

  function cacheSessionTranslation(key, result) {
    translationCache.set(key, result);
    try {
      window.sessionStorage.setItem("video-translation:" + key, JSON.stringify(result));
    } catch (_error) {
      // A full or disabled session store should not break translation.
    }
  }

  function requestTranslation(text, context) {
    const key = translationKey(text, context);
    const cached = readSessionTranslation(key);
    if (cached) {
      return Promise.resolve(cached);
    }
    if (translationRequests.has(key)) {
      return translationRequests.get(key);
    }

    const request = fetch(translateUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify({
        text: text,
        context: context || text,
        source_language: sourceLanguage,
      }),
    })
      .then(async function (response) {
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.error || "Translation failed.");
        }
        cacheSessionTranslation(key, result);
        return result;
      })
      .finally(function () {
        translationRequests.delete(key);
      });

    translationRequests.set(key, request);
    return request;
  }

  function applyTranslationResult(result, text, context, button) {
    translationSource.textContent = window.CapturePanel.headline(result, text);
    translationResult.textContent = result.translation;
    renderSuggestion(result.suggestion);
    if (!button) {
      return;
    }
    const suggestion = result.suggestion || {};
    selectedWordData = {
      word: suggestion.normalized_word || result.word || text,
      translation: result.translation,
      context: context || text,
      suggestion: suggestion,
    };
    addToGlobal.hidden = false;
    globalDeckStatus.textContent = "";
  }

  async function translateText(text, context, button) {
    const requestVersion = ++translationRequestVersion;
    clearSelectedWord();
    if (button) {
      selectedWord = button;
      selectedWord.classList.add("is-selected");
    }
    translationPanel.hidden = false;
    translationSource.textContent = text;
    translationResult.textContent = "Translating…";
    translationContext.textContent = "";
    translationPanel.setAttribute("aria-busy", "true");
    centerActiveCueAfterLayout("smooth");

    try {
      const result = await requestTranslation(text, context);
      if (requestVersion !== translationRequestVersion) {
        return;
      }
      applyTranslationResult(result, text, context, button);
    } catch (error) {
      if (requestVersion !== translationRequestVersion) {
        return;
      }
      translationResult.textContent = error.message;
    } finally {
      if (requestVersion === translationRequestVersion) {
        translationPanel.removeAttribute("aria-busy");
      }
    }
  }

  function scheduleTranslationPrefetch(button) {
    window.clearTimeout(translationPrefetchTimer);
    if (!button) {
      return;
    }
    translationPrefetchTimer = window.setTimeout(function () {
      requestTranslation(button.dataset.word, button.dataset.context).catch(function () {
        // Prefetch is opportunistic. A click will surface any provider error.
      });
    }, 180);
  }

  transcriptList.addEventListener("pointerover", function (event) {
    scheduleTranslationPrefetch(event.target.closest(".transcript-word"));
  });

  transcriptList.addEventListener("pointerout", function (event) {
    const button = event.target.closest(".transcript-word");
    if (button && !button.contains(event.relatedTarget)) {
      window.clearTimeout(translationPrefetchTimer);
    }
  });

  transcriptList.addEventListener("focusin", function (event) {
    scheduleTranslationPrefetch(event.target.closest(".transcript-word"));
  });

  function selectionIsInsideTranscript(selection) {
    if (bilingual) {
      return captionTiming.sourceSelection(selection, transcriptList);
    }
    if (!selection || selection.isCollapsed || !selection.anchorNode || !selection.focusNode) {
      return false;
    }
    return transcriptList.contains(selection.anchorNode) &&
      transcriptList.contains(selection.focusNode);
  }

  function selectionContext(selection, fallback) {
    const anchor = selection.anchorNode.nodeType === Node.ELEMENT_NODE
      ? selection.anchorNode
      : selection.anchorNode.parentElement;
    const cue = anchor ? anchor.closest(".transcript-cue") : null;
    const cueText = cue ? cue.querySelector(".transcript-text") : null;
    return cueText ? cueText.textContent.replace(/\s+/g, " ").trim() : fallback;
  }

  let suppressNextClick = false;
  transcriptList.addEventListener("mouseup", function (event) {
    if (event.button !== 0) {
      return;
    }
    const selection = window.getSelection();
    if (!selectionIsInsideTranscript(selection)) {
      return;
    }
    const text = selection.toString().replace(/\s+/g, " ").trim();
    if (!text) {
      return;
    }
    suppressNextClick = true;
    translateText(text, selectionContext(selection, text), null);
    window.setTimeout(function () {
      suppressNextClick = false;
    }, 0);
  });

  transcriptList.addEventListener("click", function (event) {
    const wordButton = event.target.closest(".transcript-word");
    if (suppressNextClick) {
      event.preventDefault();
      return;
    }
    if (wordButton) {
      translateText(wordButton.dataset.word, wordButton.dataset.context, wordButton);
      return;
    }
    const seekButton = event.target.closest("[data-seek]");
    if (seekButton && player && typeof player.seekTo === "function") {
      player.seekTo(Number(seekButton.dataset.seek), true);
      player.playVideo();
    }
  });

  const transcriptDrawerToggle = document.getElementById("transcript-drawer-toggle");

  function setTranscriptDrawer(expanded) {
    if (!transcriptDrawerToggle) {
      return;
    }
    const transcriptColumn = transcriptDrawerToggle.closest(".transcript-column");
    if (!transcriptColumn) {
      return;
    }
    transcriptColumn.classList.toggle("is-expanded", expanded);
    transcriptDrawerToggle.setAttribute("aria-expanded", String(expanded));
    transcriptDrawerToggle.setAttribute("aria-label", expanded ? "Hide transcript" : "Show transcript");
    centerActiveCueAfterLayout("auto");
  }

  function setTheaterMode(enabled) {
    document.body.classList.toggle("video-theater-mode", enabled);
    theaterToggle.setAttribute("aria-pressed", String(enabled));
    theaterLabel.textContent = enabled ? "Exit theater" : "Theater mode";
    theaterToggle.title = enabled ? "Exit theater mode (Esc)" : "Enter theater mode";
    centerActiveCueAfterLayout("auto");
  }

  theaterToggle.addEventListener("click", function () {
    setTheaterMode(!document.body.classList.contains("video-theater-mode"));
  });

  if (transcriptDrawerToggle) {
    transcriptDrawerToggle.addEventListener("click", function () {
      const expanded = transcriptDrawerToggle.getAttribute("aria-expanded") === "true";
      setTranscriptDrawer(!expanded);
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && document.body.classList.contains("video-theater-mode")) {
      setTheaterMode(false);
      theaterToggle.focus();
    }
  });

  translationClose.addEventListener("click", function () {
    translationPanel.hidden = true;
    translationRequestVersion += 1;
    translationPanel.removeAttribute("aria-busy");
    clearSelectedWord();
    const selection = window.getSelection();
    if (selection) {
      selection.removeAllRanges();
    }
    centerActiveCueAfterLayout("smooth");
  });

  setTranscriptDrawer(false);
  setTheaterMode(window.matchMedia("(min-width: 701px)").matches);
  renderTranscript();
  loadYouTubeApi();
})();
