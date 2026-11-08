(() => {
  const popup = document.getElementById('newsletter-popup');
  if (!popup || typeof popup.showModal !== 'function') return;

  const form = popup.querySelector('.newsletter-popup-form');
  const closeButton = popup.querySelector('.newsletter-popup-close');
  const emailInput = popup.querySelector('input[type="email"]');
  const submitButton = popup.querySelector('.newsletter-popup-submit');
  const feedback = popup.querySelector('.newsletter-popup-feedback');
  const suppressedUntilKey = 'newsletter-popup-suppressed-until';
  const submittedKey = 'newsletter-popup-submitted';
  const cooldownMilliseconds = 30 * 24 * 60 * 60 * 1000;

  function readStorage(key) {
    try {
      const value = window.localStorage.getItem(key);
      if (value !== null) return value;
    } catch {
      // Fall back to session storage in browsers that block persistent storage.
    }
    try {
      return window.sessionStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      try {
        window.sessionStorage.setItem(key, value);
      } catch {
        // The dialog remains dismissible when browser storage is unavailable.
      }
    }
  }

  function suppressPopup(permanently = false) {
    const key = permanently ? submittedKey : suppressedUntilKey;
    const value = permanently
      ? 'true'
      : String(Date.now() + cooldownMilliseconds);
    writeStorage(key, value);
  }

  const permanentlySuppressed = readStorage(submittedKey) === 'true';
  const suppressedUntil = Number(readStorage(suppressedUntilKey) || 0);
  if (permanentlySuppressed || suppressedUntil > Date.now()) return;

  window.setTimeout(() => {
    popup.showModal();
    emailInput.focus();
  }, 5000);

  function closePopup() {
    suppressPopup();
    popup.close();
  }

  closeButton.addEventListener('click', closePopup);
  popup.addEventListener('cancel', () => suppressPopup());
  popup.addEventListener('click', (event) => {
    if (event.target === popup) closePopup();
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;

    feedback.textContent = '';
    feedback.removeAttribute('data-success');
    submitButton.disabled = true;

    try {
      const response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        headers: {'X-Requested-With': 'XMLHttpRequest'},
      });
      const result = await response.json();
      if (!response.ok || !result.success) {
        throw new Error(result.message || 'We could not subscribe you right now. Please try again.');
      }

      feedback.textContent = result.message;
      feedback.dataset.success = 'true';
      form.querySelectorAll('input, button').forEach((field) => {
        field.disabled = true;
      });
      suppressPopup(true);
    } catch (error) {
      feedback.textContent = error instanceof Error
        ? error.message
        : 'We could not subscribe you right now. Please try again.';
    } finally {
      if (feedback.dataset.success !== 'true') {
        submitButton.disabled = false;
      }
    }
  });
})();
