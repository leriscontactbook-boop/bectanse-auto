(function () {
  "use strict";
  const toast = document.getElementById("journeyToast");
  const mtServers = window.BECTANSE_MT_SERVERS || {};
  let busy = false,
    installPrompt = null;
  const nextAnchor = {
    broker_opened: "verification",
    request_verification: "verification",
    trading_ready: "credentials",
    save_credentials: "installation",
    app_installed: "notifications",
    notifications_enabled: "notifications",
  };

  function message(text, error = false) {
    if (!toast) return;
    toast.textContent = text;
    toast.classList.toggle("error", error);
    toast.classList.add("show");
    window.setTimeout(() => toast.classList.remove("show"), 4200);
  }

  async function postAction(action, payload = {}) {
    if (busy) return null;
    busy = true;
    const buttons = document.querySelectorAll(`[data-action="${action}"]`);
    buttons.forEach((button) => {
      button.disabled = true;
      button.dataset.previous = button.innerHTML;
      button.textContent = "Validation en cours…";
    });
    try {
      const response = await fetch("/api/demarrage/action", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...payload }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(
          data.error || "La validation est momentanément indisponible",
        );
      if (window.bectanseTrack)
        window.bectanseTrack("member_activation_step", {
          step: action,
          progress: data.state && data.state.progress,
        });
      message(
        action === "request_verification"
          ? "Demande transmise à l’équipe"
          : "Étape enregistrée",
      );
      const target =
        nextAnchor[action] ||
        nextAnchor[(data.state && data.state.current_step) || ""];
      window.setTimeout(() => {
        location.href = "/demarrage" + (target ? "#" + target : "");
        location.reload();
      }, 520);
      return data;
    } catch (error) {
      message(error.message || "Une erreur est survenue", true);
      buttons.forEach((button) => {
        button.disabled = false;
        button.innerHTML = button.dataset.previous || button.innerHTML;
      });
      return null;
    } finally {
      busy = false;
    }
  }

  document.querySelectorAll("[data-action]").forEach((button) =>
    button.addEventListener("click", function () {
      const action = button.dataset.action;
      if (action === "request_verification")
        return postAction(action, {
          broker_email: (
            document.getElementById("brokerEmail")?.value || ""
          ).trim(),
          broker_reference: (
            document.getElementById("brokerReference")?.value || ""
          ).trim(),
        });
      if (action === "trading_ready")
        return postAction(action, {
          platform:
            document.querySelector(".aj-platform-switch button.active")?.dataset
              .platform || "MT5",
          funding_confirmed:
            document.getElementById("fundingConfirmed")?.checked === true,
        });
      if (action === "save_credentials")
        return postAction(action, {
          platform: document.getElementById("mtPlatform")?.value || "MT5",
          mt_login: (document.getElementById("mtLogin")?.value || "").trim(),
          mt_server: (document.getElementById("mtServer")?.value || "").trim(),
          mt_password: document.getElementById("mtPassword")?.value || "",
        });
      return postAction(action);
    }),
  );

  document.querySelectorAll(".aj-platform-switch button").forEach((button) =>
    button.addEventListener("click", function () {
      document
        .querySelectorAll(".aj-platform-switch button")
        .forEach((item) => item.classList.toggle("active", item === button));
      const select = document.getElementById("mtPlatform");
      if (select) {
        select.value = button.dataset.platform;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }),
  );
  document.querySelectorAll("[data-device]").forEach((button) =>
    button.addEventListener("click", function () {
      document
        .querySelectorAll("[data-device]")
        .forEach((item) => item.classList.toggle("active", item === button));
      document
        .querySelectorAll("[data-guide]")
        .forEach((item) =>
          item.classList.toggle(
            "active",
            item.dataset.guide === button.dataset.device,
          ),
        );
    }),
  );
  document
    .querySelector("[data-toggle-password]")
    ?.addEventListener("click", function () {
      const field = document.getElementById("mtPassword");
      if (field) field.type = field.type === "password" ? "text" : "password";
    });

  function populateServerList(platform) {
    const select = document.getElementById("mtServer");
    if (!select) return;
    const previous = select.value;
    const servers = mtServers[platform] || [];
    select.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Sélectionne le serveur reçu par e-mail";
    select.appendChild(placeholder);
    servers.forEach((server) => {
      const option = document.createElement("option");
      option.value = server;
      option.textContent = server;
      select.appendChild(option);
    });
    select.value = servers.includes(previous) ? previous : "";
    const phoneServer = document.getElementById("phoneServer");
    if (phoneServer && !select.value)
      phoneServer.textContent = "Sélectionne ton serveur";
  }

  document
    .getElementById("mtPlatform")
    ?.addEventListener("change", function () {
      populateServerList(this.value);
      const phonePlatform = document.getElementById("phonePlatform");
      if (phonePlatform) phonePlatform.textContent = this.value;
    });

  ["mtPlatform", "mtLogin", "mtServer"].forEach((id) =>
    document.getElementById(id)?.addEventListener("input", function () {
      const target = document.getElementById(
        id === "mtPlatform"
          ? "phonePlatform"
          : id === "mtLogin"
            ? "phoneLogin"
            : "phoneServer",
      );
      if (target)
        target.textContent =
          this.value || target.dataset.placeholder || this.placeholder;
    }),
  );

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
  });
  document
    .getElementById("launchInstall")
    ?.addEventListener("click", async function () {
      const standalone =
        window.matchMedia("(display-mode:standalone)").matches ||
        window.navigator.standalone === true;
      if (standalone) {
        message("Bectanse est déjà installée sur cet appareil");
        return;
      }
      if (installPrompt) {
        await installPrompt.prompt();
        await installPrompt.userChoice;
        installPrompt = null;
        message("Confirme ensuite avec le bouton C’est installé");
        return;
      }
      const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
      message(
        ios
          ? "Dans Safari, appuie sur Partager puis Sur l’écran d’accueil"
          : "Ouvre le menu du navigateur puis choisis Installer l’application",
      );
    });

  document
    .getElementById("enableNotifications")
    ?.addEventListener("click", async function () {
      const button = this,
        status = document.getElementById("notificationStatus");
      button.disabled = true;
      button.textContent = "Activation et test en cours…";
      try {
        const result = window.enablePushNotifications
          ? await window.enablePushNotifications()
          : { ok: false, reason: "unsupported" };
        if (!result.ok) {
          const reasons = {
            "install-required":
              "Sur iPhone, ajoute d’abord Bectanse à l’écran d’accueil puis ouvre l’application",
            denied:
              "Les notifications sont bloquées dans les réglages de cet appareil",
            unsupported:
              "Cet appareil ne prend pas en charge les notifications Web",
          };
          throw new Error(
            reasons[result.reason] ||
              "Impossible d’activer les notifications sur cet appareil",
          );
        }
        if (status)
          status.textContent = result.testSent
            ? "Notification de test envoyée sur cet appareil"
            : "Notifications enregistrées";
        await postAction("notifications_enabled");
      } catch (error) {
        message(error.message, true);
        if (status) status.textContent = error.message;
        button.disabled = false;
        button.textContent = "Activer et tester";
      }
    });

  const observed = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        document
          .querySelectorAll("[data-rail-step]")
          .forEach((link) =>
            link.classList.toggle(
              "viewing",
              link.dataset.railStep === entry.target.dataset.step,
            ),
          );
      });
    },
    { rootMargin: "-30% 0px -55%", threshold: 0 },
  );
  document
    .querySelectorAll(".aj-stage")
    .forEach((stage) => observed.observe(stage));
  document
    .querySelector("[data-broker-link]")
    ?.addEventListener("click", () =>
      message(
        "Le formulaire officiel s’ouvre, reviens ici dès que le compte est créé",
      ),
    );
  document.querySelectorAll("input").forEach((input) =>
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    }),
  );
  document.querySelectorAll("[data-rail-step]").forEach((link) =>
    link.addEventListener("click", function (event) {
      const target = document.getElementById(link.dataset.railStep);
      if (target && target.classList.contains("locked")) {
        event.preventDefault();
        message(
          "Cette étape se débloque dès que la précédente est terminée",
          true,
        );
      }
    }),
  );
  const savedStep = window.BECTANSE_ACTIVATION_STATE?.current_step;
  const initialTarget =
    location.hash ||
    (savedStep && savedStep !== "broker" ? "#" + savedStep : "");
  if (initialTarget) {
    window.setTimeout(
      () =>
        document
          .querySelector(initialTarget)
          ?.scrollIntoView({ behavior: "smooth", block: "start" }),
      220,
    );
  }
})();
