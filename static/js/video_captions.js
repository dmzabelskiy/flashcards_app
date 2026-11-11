// Independent caption timing and reference text, shared by browser and Node tests.
(function (root) {
  "use strict";

  function timedCues(items) {
    return items.map(function (cue) {
      const start = Number(cue.start);
      return {
        text: String(cue.text),
        start: start,
        end: start + Math.max(0, Number(cue.duration)),
      };
    }).filter(function (cue) {
      return Number.isFinite(cue.start) && Number.isFinite(cue.end) && cue.end > cue.start;
    }).sort(function (a, b) { return a.start - b.start; });
  }

  function overlappingCues(cues, start, end) {
    return cues.filter(function (cue) {
      return cue.start < end && cue.end > start;
    });
  }

  function currentText(cues, seconds) {
    return cues.filter(function (cue) {
      return cue.start <= seconds && seconds < cue.end;
    }).map(function (cue) { return cue.text; }).join(" ");
  }

  function setReferenceText(element, text) {
    // Never parse caption contents as HTML or create dictionary word buttons.
    element.textContent = text;
  }

  function sourceSelection(selection, transcriptList) {
    if (!selection || selection.isCollapsed || !selection.rangeCount) {
      return false;
    }
    function sourceElement(node) {
      const element = node && (node.nodeType === 1 ? node : node.parentElement);
      const source = element && element.closest(".transcript-text");
      return source && transcriptList.contains(source);
    }
    if (!sourceElement(selection.anchorNode) || !sourceElement(selection.focusNode)) {
      return false;
    }
    // Dutch endpoints can still enclose English rows. Reject the whole selection
    // instead of sending mixed-language text to the Dutch dictionary/providers.
    for (let index = 0; index < selection.rangeCount; index += 1) {
      const range = selection.getRangeAt(index);
      for (const reference of transcriptList.querySelectorAll(".transcript-reference")) {
        if (range.intersectsNode(reference)) {
          return false;
        }
      }
    }
    return true;
  }

  const captions = {
    timedCues: timedCues,
    overlappingCues: overlappingCues,
    currentText: currentText,
    setReferenceText: setReferenceText,
    sourceSelection: sourceSelection,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = captions;
  } else {
    root.VideoCaptions = captions;
  }
})(typeof window !== "undefined" ? window : globalThis);
