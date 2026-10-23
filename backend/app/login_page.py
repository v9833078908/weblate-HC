# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import posixpath
from urllib.parse import unquote, urlsplit


def render_login_page(destination: str = "") -> str:
    fallback = "/console/projects"
    decoded = unquote(destination)
    if (
        not destination.startswith("/console/")
        or not decoded.startswith("/console/")
        or any(ord(char) < 32 or char == "\\" for char in decoded)
    ):
        destination = fallback
    else:
        parsed = urlsplit(decoded)
        if not posixpath.normpath(parsed.path).startswith("/console/"):
            destination = fallback
    target = json.dumps(destination or fallback, ensure_ascii=False)
    # The destination is JavaScript data, never HTML or executable source.
    target = target.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return PAGE.replace("__RETURN_TO__", target)


PAGE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Вход в визард — HCGameLoc</title>
  <style>
    :root {color:#17211d;background:#f4f6f5;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-synthesis:none}
    * {box-sizing:border-box}
    body {margin:0;min-inline-size:0;min-block-size:100vh}
    .topbar {display:flex;align-items:center;min-block-size:3.25rem;padding-inline:clamp(1rem,4vw,2.5rem);color:white;background:#173e34}
    .brand {font-weight:700}
    main {display:grid;justify-items:center;padding:clamp(2rem,8vw,5rem) 1rem}
    .login-card {inline-size:min(28rem,100%);border:1px solid #cad4d0;border-radius:.6rem;padding:clamp(1.25rem,4vw,2rem);background:white;box-shadow:0 .5rem 2rem rgb(23 62 52 / 6%)}
    h1 {font-size:clamp(1.75rem,5vw,2rem);line-height:1.2;margin:0 0 .75rem}
    .lead {margin:0 0 1.5rem;color:#47645b;line-height:1.5}
    form {display:grid;gap:1rem}
    label {display:grid;gap:.4rem;font-weight:700}
    input,button {font:inherit;border-radius:.35rem;min-block-size:2.75rem;inline-size:100%;min-inline-size:0}
    input {border:1px solid #9aaba5;padding:.65rem .75rem;background:white;color:#17211d}
    button {border:1px solid #245f50;padding:.65rem 1rem;color:white;background:#245f50;cursor:pointer;font-weight:700}
    button:hover {background:#173e34}
    button:disabled {cursor:wait;opacity:.65}
    :focus-visible {outline:3px solid #f6b73c;outline-offset:3px}
    .error {border-inline-start:4px solid #b42318;padding:.8rem 1rem;margin:1rem 0 0;color:#7a271a;background:#fef3f2;line-height:1.5;overflow-wrap:anywhere}
  </style>
</head>
<body>
  <header class="topbar"><span class="brand">HCGameLoc</span></header>
  <main>
    <section class="login-card" aria-labelledby="login-heading">
      <h1 id="login-heading">Вход в визард</h1>
      <p class="lead">Войдите, чтобы продолжить работу с проектами.</p>
      <form id="login">
        <label for="username">Имя пользователя<input id="username" name="username" required autocomplete="username" autocapitalize="none" spellcheck="false"></label>
        <label for="password">Пароль<input id="password" name="password" type="password" required autocomplete="current-password"></label>
        <button id="submit" type="submit">Войти</button>
      </form>
      <p id="error" class="error" role="alert" tabindex="-1" hidden></p>
    </section>
  </main>
  <script>
    const form = document.querySelector('#login');
    const submit = document.querySelector('#submit');
    const error = document.querySelector('#error');
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (submit.disabled) return;
      const body = Object.fromEntries(new FormData(form));
      error.hidden = true;
      error.textContent = '';
      submit.disabled = true;
      submit.textContent = 'Входим…';
      form.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch('/auth/login', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body)
        });
        if (!response.ok) throw new Error('Login failed');
        location.assign(__RETURN_TO__);
      } catch {
        error.textContent = 'Не удалось войти. Проверьте данные или повторите позднее.';
        error.hidden = false;
        error.focus();
      } finally {
        submit.disabled = false;
        submit.textContent = 'Войти';
        form.setAttribute('aria-busy', 'false');
      }
    };
  </script>
</body>
</html>"""
