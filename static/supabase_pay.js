(function () {
  var ORDER_ID = window.ORDER_ID;
  var VIEW_TOKEN = window.VIEW_TOKEN;
  var ENTRY_FEE = window.ENTRY_FEE;
  var BKASH_NUMBER = window.BKASH_NUMBER;
  var NAGAD_NUMBER = window.NAGAD_NUMBER;
  var SUPABASE_URL = window.SUPABASE_URL;
  var SUPABASE_ANON_KEY = window.SUPABASE_ANON_KEY;
  var SUPABASE_TABLE = window.SUPABASE_TABLE;

  var selectedMethod = null;
  var pollTimer = null;
  var verifying = false;
  var knownTrxIds = {};

  var els = {
    methodScreen: document.getElementById("methodScreen"),
    paymentScreen: document.getElementById("paymentScreen"),
    cardBkash: document.getElementById("cardBkash"),
    cardNagad: document.getElementById("cardNagad"),
    btnContinue: document.getElementById("btnContinue"),
    btnVerify: document.getElementById("btnVerify"),
    trxId: document.getElementById("trxId"),
    instructions: document.getElementById("instructionsList"),
    instructionBox: document.getElementById("instructionBox"),
    statusBox: document.getElementById("statusBox"),
    autoPoll: document.getElementById("autoPoll"),
  };

  function showStatus(msg, type) {
    var el = els.statusBox;
    el.textContent = msg;
    el.className = "status-box show " + type;
  }

  function hideStatus() {
    els.statusBox.className = "status-box";
  }

  function selectMethod(method) {
    selectedMethod = method;
    els.cardBkash.classList.remove("selected", "nagad");
    els.cardNagad.classList.remove("selected", "nagad");
    if (method === "bkash") {
      els.cardBkash.classList.add("selected");
    } else {
      els.cardNagad.classList.add("selected", "nagad");
    }
    els.btnContinue.disabled = false;
  }

  function bkashSteps(num, amt) {
    return (
      "<li>Dial *247# or open the bKash app</li>" +
      '<li>Tap <strong>"Send Money"</strong></li>' +
      "<li>Number: <strong>" + num + "</strong></li>" +
      "<li>Amount: <strong>৳" + amt + "</strong> (minimum — less er TrxID accept hobe na)</li>" +
      "<li>Confirm with PIN — you will receive an SMS from bKash</li>" +
      "<li>Enter TrxID to verify</li>"
    );
  }

  function nagadSteps(num, amt) {
    return (
      "<li>Dial *167# or open the Nagad app</li>" +
      '<li>Select <strong>"Send Money"</strong></li>' +
      "<li>Number: <strong>" + num + "</strong></li>" +
      "<li>Amount: <strong>৳" + amt + "</strong> (minimum — less er TxID accept hobe na)</li>" +
      "<li>Confirm with PIN — you will receive an SMS from Nagad</li>" +
      "<li>Enter TxID to verify</li>"
    );
  }

  function showPaymentScreen() {
    els.methodScreen.style.display = "none";
    els.paymentScreen.style.display = "block";

    if (selectedMethod === "nagad") {
      els.instructionBox.style.background = "#e85c0d";
      els.instructions.innerHTML = nagadSteps(NAGAD_NUMBER, ENTRY_FEE);
    } else {
      els.instructionBox.style.background = "#e2136e";
      els.instructions.innerHTML = bkashSteps(BKASH_NUMBER, ENTRY_FEE);
    }
    hideStatus();
    loadSnapshot().then(startPoll).catch(function (err) {
      showStatus("Supabase init: " + err.message, "error");
    });
  }

  function backToMethod() {
    stopPoll();
    els.paymentScreen.style.display = "none";
    els.methodScreen.style.display = "block";
  }

  function supabaseHeaders() {
    return {
      apikey: SUPABASE_ANON_KEY,
      Authorization: "Bearer " + SUPABASE_ANON_KEY,
      "Content-Type": "application/json",
    };
  }

  function tableUrl(query) {
    var base = SUPABASE_URL + "/rest/v1/" + SUPABASE_TABLE;
    return query ? base + "?" + query : base;
  }

  function listRecentUnused() {
    var params = "status=eq.unused&select=trx_id,amount,status&limit=20";
    return fetch(tableUrl(params), { headers: supabaseHeaders() }).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (t) {
          throw new Error("Poll failed " + res.status + ": " + t);
        });
      }
      return res.json();
    });
  }

  function loadSnapshot() {
    return listRecentUnused().then(function (rows) {
      rows.forEach(function (r) {
        if (r.trx_id) knownTrxIds[r.trx_id] = true;
      });
    });
  }

  function findNewRow(rows) {
    for (var i = 0; i < rows.length; i++) {
      var id = rows[i].trx_id;
      if (id && !knownTrxIds[id]) {
        return rows[i];
      }
    }
    return null;
  }

  function verifyOnServer(trxId) {
    if (!trxId || !trxId.trim() || verifying) return;
    verifying = true;
    els.btnVerify.disabled = true;
    showStatus("Verifying transaction...", "info");

    var fd = new FormData();
    fd.append("order_id", ORDER_ID);
    fd.append("trx_id", trxId.trim());
    fd.append("t", VIEW_TOKEN);

    fetch("/api/verify-supabase-payment", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          showStatus("Payment verified! Redirecting...", "success");
          setTimeout(function () {
            window.location.href = data.redirect;
          }, 600);
        } else {
          showStatus(data.error || "Verification failed", "error");
          verifying = false;
          els.btnVerify.disabled = false;
        }
      })
      .catch(function () {
        showStatus("Network error — please try again", "error");
        verifying = false;
        els.btnVerify.disabled = false;
      });
  }

  function pollOnce() {
    if (!els.autoPoll.checked || verifying) return;
    listRecentUnused()
      .then(function (rows) {
        var latest = findNewRow(rows);
        if (latest) {
          knownTrxIds[latest.trx_id] = true;
          els.trxId.value = latest.trx_id;
          showStatus("New payment: " + latest.trx_id + " (৳" + latest.amount + ") — verifying...", "info");
          verifyOnServer(latest.trx_id);
        } else {
          showStatus("Waiting for payment...", "info");
        }
      })
      .catch(function (err) {
        showStatus("Poll error: " + err.message, "error");
      });
  }

  function startPoll() {
    stopPoll();
    if (!els.autoPoll.checked) return;
    pollOnce();
    pollTimer = setInterval(pollOnce, 3000);
  }

  function stopPoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  els.cardBkash.addEventListener("click", function () { selectMethod("bkash"); });
  els.cardNagad.addEventListener("click", function () { selectMethod("nagad"); });

  els.btnContinue.addEventListener("click", function () {
    if (!selectedMethod) return;
    showPaymentScreen();
  });

  els.btnVerify.addEventListener("click", function () {
    verifyOnServer(els.trxId.value);
  });

  els.autoPoll.addEventListener("change", function () {
    if (els.autoPoll.checked) startPoll();
    else stopPoll();
  });
})();