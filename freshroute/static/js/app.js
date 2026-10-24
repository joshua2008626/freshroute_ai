/* FreshRoute AI - small progressive-enhancement helpers (no inline scripts: CSP-friendly). */
(function () {
  'use strict';
  var body = document.body;

  // Mobile navigation drawer
  var btn = document.getElementById('menu-btn');
  if (btn) {
    var setOpen = function (open) {
      body.classList.toggle('nav-open', open);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    btn.addEventListener('click', function () { setOpen(!body.classList.contains('nav-open')); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setOpen(false); });
    document.addEventListener('click', function (e) {
      if (body.classList.contains('nav-open') && !e.target.closest('#sidebar') && !e.target.closest('#menu-btn')) setOpen(false);
    });
  }

  // Auto-submit selects (data-source switcher)
  document.querySelectorAll('[data-autosubmit]').forEach(function (el) {
    el.addEventListener('change', function () { el.form.submit(); });
  });

  // Confirmation prompts for destructive forms/buttons
  document.querySelectorAll('[data-confirm]').forEach(function (el) {
    var target = el.tagName === 'FORM' ? el : el;
    var evt = el.tagName === 'FORM' ? 'submit' : 'click';
    target.addEventListener(evt, function (e) {
      if (!window.confirm(el.getAttribute('data-confirm'))) e.preventDefault();
    });
  });

  // Disable submit buttons while a request is running, and show a loading label
  document.querySelectorAll('form[data-loading]').forEach(function (form) {
    form.addEventListener('submit', function () {
      var b = form.querySelector('button[type=submit]');
      if (b && form.checkValidity()) { b.disabled = true; b.textContent = form.getAttribute('data-loading'); }
    });
  });

  // Drag-and-drop file zone
  var zone = document.getElementById('dropzone');
  if (zone) {
    var input = zone.querySelector('input[type=file]');
    var label = document.getElementById('file-label');
    var submit = document.getElementById('upload-submit');
    var show = function () {
      if (input.files && input.files[0]) {
        label.textContent = input.files[0].name + ' (' + Math.max(1, Math.round(input.files[0].size / 1024)) + ' KB)';
        if (submit) submit.disabled = false;
      }
    };
    ['dragenter', 'dragover'].forEach(function (t) {
      zone.addEventListener(t, function (e) { e.preventDefault(); zone.classList.add('drag'); });
    });
    ['dragleave', 'drop'].forEach(function (t) {
      zone.addEventListener(t, function (e) { e.preventDefault(); zone.classList.remove('drag'); });
    });
    zone.addEventListener('drop', function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
    });
    input.addEventListener('change', show);
    zone.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
    zone.addEventListener('click', function (e) { if (e.target !== input) input.click(); });
  }

  // Print button
  document.querySelectorAll('[data-print]').forEach(function (el) {
    el.addEventListener('click', function () { window.print(); });
  });

  // Live "delta vs usual" for plan inputs
  document.querySelectorAll('input[data-usual]').forEach(function (inp) {
    var out = document.getElementById(inp.getAttribute('data-delta'));
    var upd = function () {
      var usual = parseFloat(inp.getAttribute('data-usual')), v = parseFloat(inp.value);
      if (!out) return;
      if (isNaN(v) || isNaN(usual)) { out.textContent = '-'; return; }
      var d = v - usual; out.textContent = (d > 0 ? '+' : '') + (Math.round(d * 10) / 10);
    };
    inp.addEventListener('input', upd); upd();
  });
})();
