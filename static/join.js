(function () {
  const SQUAD_SIZE = window.SQUAD_SIZE || 4;
  const FF_UID_MIN = window.FF_UID_MIN || 8;
  const FF_UID_MAX = window.FF_UID_MAX || 12;
  const lookupCache = {};

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function block(i) {
    return document.querySelector(`.player-block[data-i="${i}"]`);
  }

  function playerInput(i) {
    return $(`input[name="player_${i}"]`, block(i));
  }

  function isUid(value) {
    value = (value || "").trim();
    return /^\d+$/.test(value) && value.length >= FF_UID_MIN && value.length <= FF_UID_MAX;
  }

  function clearResolved(i) {
    $(`.resolved-input`, block(i)).value = "";
    $(`.resolved-uid`, block(i)).value = "";
    const el = $(`.ff-resolved-name`, block(i));
    if (el) {
      el.textContent = "";
      el.style.display = "none";
    }
  }

  function applyFetchedName(i, uid, name) {
    const inp = playerInput(i);
    if (inp && name) inp.value = name;
    $(`.resolved-input`, block(i)).value = name;
    $(`.resolved-uid`, block(i)).value = uid;
    const el = $(`.ff-resolved-name`, block(i));
    if (el) {
      el.textContent = "";
      el.style.display = "none";
    }
    setError(i, "");
    toggleFetchButton(i);
  }

  function setLoading(i, on, msg) {
    const el = $(`.ff-uid-loading`, block(i));
    if (!el) return;
    el.textContent = on ? msg || "খুঁজছি..." : "";
    el.style.display = on ? "block" : "none";
  }

  function setError(i, msg) {
    const el = $(`.ff-uid-error`, block(i));
    if (!el) return;
    el.textContent = msg || "";
    el.style.display = msg ? "block" : "none";
  }

  function toggleFetchButton(i) {
    const inp = playerInput(i);
    const btn = $(`.lookup-btn`, block(i));
    if (!inp || !btn) return;
    const val = inp.value.trim();
    const show = isUid(val);
    btn.classList.toggle("hidden", !show);
    if (!show) {
      setLoading(i, false);
      if (!val) setError(i, "");
    }
  }

  async function fetchNameFromUid(i, uid) {
    uid = (uid || "").trim();
    if (!isUid(uid)) {
      clearResolved(i);
      setError(i, "");
      return false;
    }

    if (lookupCache[uid]) {
      applyFetchedName(i, uid, lookupCache[uid]);
      return true;
    }

    setLoading(i, true);
    setError(i, "");
    try {
      const srv = (window.FF_SERVER || "bd").trim();
      const res = await fetch(
        `/api/lookup-uid?uid=${encodeURIComponent(uid)}&server=${encodeURIComponent(srv)}`
      );
      const data = await res.json();
      if (!data.ok) {
        clearResolved(i);
        setError(i, data.error || "UID পাওয়া যায়নি");
        return false;
      }
      lookupCache[uid] = data.name;
      applyFetchedName(i, uid, data.name);
      if (data.mock) setError(i, "Test mode — API off");
      return true;
    } finally {
      setLoading(i, false);
    }
  }

  function onPlayerInput(i) {
    const inp = playerInput(i);
    if (!inp) return;
    const val = inp.value.trim();
    const prevUid = $(`.resolved-uid`, block(i))?.value || "";

    toggleFetchButton(i);

    if (isUid(val)) {
      if (prevUid && prevUid !== val) {
        clearResolved(i);
        delete lookupCache[prevUid];
      }
      if (prevUid !== val) {
        $(`.resolved-uid`, block(i)).value = "";
      }
    } else {
      clearResolved(i);
      setError(i, "");
      setLoading(i, false);
    }
  }

  document.querySelectorAll(".player-input").forEach((inp) => {
    const i = inp.dataset.i;
    inp.addEventListener("input", () => onPlayerInput(i));
    inp.addEventListener("blur", () => onPlayerInput(i));
  });

  document.querySelectorAll(".lookup-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = btn.dataset.i;
      fetchNameFromUid(i, playerInput(i)?.value);
    });
  });

  const form = document.getElementById("joinForm");
  if (!form) return;

  const gatewayPay = document.getElementById("gatewayPay");
  const payPageError = document.getElementById("payPageError");
  const submitBtn = document.getElementById("submitBtn");
  const gatewayEnabled = !!window.GATEWAY_ENABLED;
  const manualPayment = !!window.MANUAL_PAYMENT;

  function syncPayUI() {
    if (submitBtn) {
      if (manualPayment) {
        submitBtn.textContent = "Submit Order (৳" + (window.ENTRY_FEE || "—") + ")";
      } else {
        submitBtn.textContent = gatewayEnabled ? "Pay ৳ Online" : "Submit Order";
      }
    }
  }

  syncPayUI();

  async function copyText(txt) {
    txt = String(txt || "").trim();
    if (!txt) return false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(txt);
        return true;
      }
    } catch {}
    try {
      const ta = document.createElement("textarea");
      ta.value = txt;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      return true;
    } catch {}
    return false;
  }

  document.querySelectorAll(".ff-copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const val = btn.dataset.copy || "";
      const ok = await copyText(val);
      const old = btn.textContent;
      btn.textContent = ok ? "Copied" : "Copy failed";
      setTimeout(() => (btn.textContent = old), 900);
    });
  });

  const params = new URLSearchParams(window.location.search);
  if (params.get("pay_error") && payPageError) {
    payPageError.textContent = decodeURIComponent(params.get("pay_error").replace(/\+/g, " "));
    payPageError.classList.remove("hidden");
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#formError");
    const btn = $("#submitBtn");
    err.classList.add("hidden");
    btn.disabled = true;

    const fd = new FormData(form);
    fd.set("payment_mode", "gateway");

    for (let i = 1; i <= SQUAD_SIZE; i++) {
      const raw = (fd.get(`player_${i}`) || "").trim();
      if (!raw) {
        err.textContent = `Player ${i}: enter name or UID`;
        err.classList.remove("hidden");
        btn.disabled = false;
        return;
      }

      if (isUid(raw)) {
        const cachedUid = $(`.resolved-uid`, block(i))?.value?.trim();
        let resolved = $(`.resolved-input`, block(i))?.value?.trim();
        if (!resolved || cachedUid !== raw) {
          const ok = await fetchNameFromUid(i, raw);
          resolved = playerInput(i)?.value?.trim();
          if (!ok || !resolved) {
            err.textContent = `Player ${i}: tap Fetch — game name required`;
            err.classList.remove("hidden");
            btn.disabled = false;
            return;
          }
        }
        fd.set(`player_${i}`, resolved);
      } else {
        fd.set(`player_${i}`, raw);
        clearResolved(i);
      }
    }

    const res = await fetch(form.dataset.submitUrl, { method: "POST", body: fd });
    const data = await res.json();
    if (!data.ok) {
      err.textContent = data.error;
      err.classList.remove("hidden");
      btn.disabled = false;
      return;
    }
    try {
      const c = String(fd.get("leader_contact") || "").trim();
      if (c) localStorage.setItem("ff_contact", c);
      const red = String(data.redirect || "").trim();
      // Manual payment e status page e direct redirect hoy — eta save kore rakhi
      if (red && red.indexOf("/join/status/") === 0) {
        localStorage.setItem("ff_last_status_url", red);
      }
    } catch (e) {}
    window.location = data.redirect;
  });

  for (let i = 1; i <= SQUAD_SIZE; i++) onPlayerInput(String(i));
})();
