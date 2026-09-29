(function () {
  function validationMessage(field) {
    const value = field.value.trim();
    if (field.required && !value) return 'This field is required.';

    if (field.name === 'first_name' || field.name === 'last_name') {
      if (/\d/.test(value)) return 'Numbers are not allowed in names.';
    }

    if (field.name === 'password') {
      if (value.length < 8 || value.length > 10) {
        return 'Password must be 8 to 10 characters long.';
      }
      if (!/\d/.test(value)) return 'Password must include at least one number.';
      if (!/[^A-Za-z0-9\s]/.test(value)) {
        return 'Password must include at least one special character.';
      }
    }

    if (field.name === 'zip_code' && value && !/^[1-9][0-9]{5}$/.test(value)) {
      return 'Enter a valid 6-digit Indian PIN code.';
    }
    if (field.name === 'phone' && value && !/^[6-9][0-9]{9}$/.test(value)) {
      return 'Enter a valid 10-digit Indian phone number.';
    }
    if (
      field.type === 'email' && value &&
      (!field.validity.valid || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value))
    ) {
      return 'Enter a valid email address.';
    }
    if (field.maxLength > -1 && value.length > field.maxLength) {
      return `Use no more than ${field.maxLength} characters.`;
    }
    return '';
  }

  function feedbackFor(field) {
    if (
      field.nextElementSibling &&
      field.nextElementSibling.classList.contains('invalid-feedback')
    ) {
      field.nextElementSibling.setAttribute('data-client-feedback', '');
      return field.nextElementSibling;
    }

    const feedback = document.createElement('div');
    feedback.className = 'invalid-feedback';
    feedback.setAttribute('data-client-feedback', '');
    field.insertAdjacentElement('afterend', feedback);
    return feedback;
  }

  function validateField(field) {
    if (!field.matches('input, select, textarea') || field.type === 'hidden') {
      return '';
    }

    const message = validationMessage(field);
    field.classList.toggle('is-invalid', Boolean(message));
    field.setAttribute('aria-invalid', String(Boolean(message)));
    feedbackFor(field).textContent = message;
    return message;
  }

  document.querySelectorAll('[data-validate-on-blur]').forEach(function (form) {
    const touchedFields = new WeakSet();

    form.addEventListener('focusout', function (event) {
      const field = event.target;
      if (!field.matches('input, select, textarea') || field.type === 'hidden') return;
      touchedFields.add(field);
      validateField(field);
    });

    form.addEventListener('input', function (event) {
      if (touchedFields.has(event.target)) validateField(event.target);
    });

    form.addEventListener('change', function (event) {
      if (touchedFields.has(event.target)) validateField(event.target);
    });

    form.addEventListener('submit', function (event) {
      const fields = Array.from(form.querySelectorAll('input, select, textarea'));
      const firstInvalid = fields.find(function (field) {
        if (field.type === 'hidden' || field.disabled) return false;
        touchedFields.add(field);
        return Boolean(validateField(field));
      });

      if (firstInvalid) {
        event.preventDefault();
        firstInvalid.focus();
      }
    });
  });
}());