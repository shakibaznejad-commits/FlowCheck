(() => {
  let form = document.querySelector("#login-form");
  let emailInput = document.querySelector("#email");
  let passwordInput = document.querySelector("#password");
  let feedback = document.querySelector("#feedback");
  const behavior = window.DEMO_BEHAVIOR;
  const sessionKey = "northline-workspace-session";
  const validEmail = "demo@example.com";
  const validPassword = "CorrectPass123!";
  const knownWrongPassword = "WrongPass123!";

  emailInput.required = behavior.requireEmail;

  function showFeedback(message) {
    feedback.textContent = message;
    feedback.hidden = false;
  }

  function clearFeedback() {
    feedback.textContent = "";
    feedback.hidden = true;
  }

  function isAuthenticated() {
    return sessionStorage.getItem(sessionKey) === "active";
  }

  function showLogin() {
    document.querySelector(".auth-layout")?.remove();
    const template = document.querySelector("#workspace-template");
    document.querySelector("main").insertBefore(template.content.cloneNode(true), document.querySelector(".page-footer"));
    document.querySelector("#logout").addEventListener("click", () => {
      if (behavior.invalidateOnLogout) sessionStorage.removeItem(sessionKey);
      history.replaceState({}, "", "/");
      showLoginForm();
    });
  }

  function showLoginForm() {
    document.querySelector(".workspace-card")?.remove();
    const section = document.createElement("section");
    section.className = "auth-layout";
    section.setAttribute("aria-labelledby", "page-title");
    section.innerHTML = `
      <div class="intro-panel">
        <p class="eyebrow">YOUR TEAM, IN SYNC</p>
        <h1>Make room for<br /><em>good work.</em></h1>
        <p class="intro-copy">A calmer place to plan, share, and move your projects forward.</p>
        <div class="workspace-art" aria-hidden="true"><div class="art-window"><div class="art-toolbar"><i></i><i></i><i></i><span>Workspace overview</span><b>•••</b></div><div class="art-content"><div class="art-rail"><b></b><i></i><i></i><i></i></div><div class="art-board"><div class="art-board-head"><span></span><span></span></div><div class="art-card"><b></b><i></i><i></i><small></small></div><div class="art-card art-card-short"><b></b><i></i><small></small></div></div></div></div><span class="art-orbit art-orbit-one"></span><span class="art-orbit art-orbit-two"></span></div>
        <p class="privacy-note"><span aria-hidden="true">✳</span> Thoughtful work starts with a little breathing room.</p>
      </div>
      <div class="form-panel"><div class="form-heading"><p class="eyebrow">NORTHLINE WORKSPACE</p><h2 id="page-title">Sign in to your workspace</h2><p>Welcome back. Enter your details to continue.</p></div>
      <form id="login-form" novalidate><label for="email">Email</label><div class="input-wrap"><span class="input-icon" aria-hidden="true">@</span><input id="email" name="email" type="email" autocomplete="username" placeholder="you@company.com" /></div><div class="label-row"><label for="password">Password</label></div><div class="input-wrap"><span class="input-icon lock-icon" aria-hidden="true">▣</span><input id="password" name="password" type="password" autocomplete="current-password" placeholder="Enter your password" required /></div><p id="feedback" class="feedback" role="status" aria-live="polite" hidden></p><button class="primary-button" type="submit"><span>Sign in</span><span aria-hidden="true">→</span></button></form><p class="form-footnote">Secure access for your team's workspace</p></div>`;
    document.querySelector("main").insertBefore(section, document.querySelector(".page-footer"));
    const restoredForm = section.querySelector("#login-form");
    form = restoredForm;
    const restoredEmail = section.querySelector("#email");
    emailInput = restoredEmail;
    passwordInput = section.querySelector("#password");
    feedback = section.querySelector("#feedback");
    restoredEmail.required = behavior.requireEmail;
    restoredForm.addEventListener("submit", handleSubmit);
  }

  function handleSubmit(event) {
    event.preventDefault();
    clearFeedback();
    const email = emailInput.value.trim().toLowerCase();
    const password = passwordInput.value;

    if (behavior.requireEmail && !email) {
      emailInput.setCustomValidity("Enter your email address.");
      emailInput.reportValidity();
      emailInput.setCustomValidity("");
      return;
    }
    if (!password) {
      passwordInput.setCustomValidity("Enter your password.");
      passwordInput.reportValidity();
      passwordInput.setCustomValidity("");
      return;
    }
    if (email && !emailInput.validity.valid) {
      emailInput.setCustomValidity("Enter a valid email address.");
      emailInput.reportValidity();
      emailInput.setCustomValidity("");
      return;
    }

    const recognizedAccount = email === validEmail;
    const acceptedPassword = password === validPassword || (
      behavior.acceptSpecificWrongPassword && password === knownWrongPassword
    );
    emailInput.value = "";
    passwordInput.value = "";

    if (recognizedAccount && acceptedPassword) {
      sessionStorage.setItem(sessionKey, "active");
      history.pushState({}, "", "/workspace");
      showLogin();
      return;
    }
    if (!email && !behavior.requireEmail) {
      showFeedback("Invalid sign-in details. Check your email and password.");
      return;
    }
    showFeedback(recognizedAccount ? "That password does not match this account." : "No account found for that email.");
  }

  form.addEventListener("submit", handleSubmit);
  window.addEventListener("popstate", () => {
    if (location.pathname === "/workspace") {
      if (isAuthenticated()) showLogin();
      else { history.replaceState({}, "", "/"); showLoginForm(); }
    } else if (isAuthenticated()) showLogin();
    else if (!document.querySelector("#login-form")) showLoginForm();
  });

  if (location.pathname === "/workspace") {
    if (isAuthenticated()) showLogin();
    else { history.replaceState({}, "", "/"); }
  }
})();
