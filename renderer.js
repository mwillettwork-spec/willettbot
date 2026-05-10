// Copyright (c) 2026 WillettBot Inc. All rights reserved.
// Proprietary and confidential. No reproduction, distribution, or use
// without express written permission.

const { ipcRenderer } = require('electron')

document.getElementById('root').innerHTML = `
  <div style="max-width:600px;margin:40px auto;background:white;border-radius:16px;padding:40px;box-shadow:0 4px 24px rgba(0,0,0,0.08)">
    <h1 style="font-size:28px;font-weight:700;margin-bottom:4px">WillettBot</h1>
    <p style="color:#888;margin-bottom:32px">Auto Emailer</p>

    <label style="display:block;margin-bottom:16px">
      <span style="font-size:13px;font-weight:600;color:#444;display:block;margin-bottom:6px">YOUR GMAIL ADDRESS</span>
      <input id="sender" type="email" placeholder="you@gmail.com"
        style="width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:15px"/>
    </label>

    <label style="display:block;margin-bottom:16px">
      <span style="font-size:13px;font-weight:600;color:#444;display:block;margin-bottom:6px">APP PASSWORD (no spaces)</span>
      <input id="password" type="password" placeholder="16-character app password"
        style="width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:15px"/>
    </label>

    <label style="display:block;margin-bottom:16px">
      <span style="font-size:13px;font-weight:600;color:#444;display:block;margin-bottom:6px">RECIPIENTS (comma separated, no spaces)</span>
      <input id="recipients" type="text" placeholder="friend@email.com,boss@work.com"
        style="width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:15px"/>
    </label>

    <label style="display:block;margin-bottom:16px">
      <span style="font-size:13px;font-weight:600;color:#444;display:block;margin-bottom:6px">SUBJECT</span>
      <input id="subject" type="text" placeholder="Weekly Update"
        style="width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:15px"/>
    </label>

    <label style="display:block;margin-bottom:24px">
      <span style="font-size:13px;font-weight:600;color:#444;display:block;margin-bottom:6px">MESSAGE BODY</span>
      <textarea id="body" rows="4" placeholder="Type your email message here..."
        style="width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:15px;resize:vertical"></textarea>
    </label>

    <!-- Mode selector tabs -->
    <div style="display:flex;gap:8px;margin-bottom:16px">
      <button class="mode-btn active" data-mode="now" style="flex:1;padding:10px;border:1.5px solid #1a1a1a;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;background:#1a1a1a;color:white">
        Send Now
      </button>
      <button class="mode-btn" data-mode="once" style="flex:1;padding:10px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;background:white;color:#444">
        Send Once At...
      </button>
      <button class="mode-btn" data-mode="recurring" style="flex:1;padding:10px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;background:white;color:#444">
        Recurring
      </button>
    </div>

    <!-- Send Once panel -->
    <div id="panel-once" style="display:none;background:#f9f9f9;border-radius:12px;padding:20px;margin-bottom:24px">
      <p style="font-size:13px;color:#888;margin-bottom:14px">Pick a date and time — WillettBot will send it once and stop.</p>
      <div style="display:flex;gap:12px">
        <div style="flex:1">
          <span style="font-size:12px;color:#888;display:block;margin-bottom:6px">DATE</span>
          <input id="once-date" type="date"
            style="width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:15px;background:white"/>
        </div>
        <div style="flex:1">
          <span style="font-size:12px;color:#888;display:block;margin-bottom:6px">TIME</span>
          <input id="once-time" type="time"
            style="width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:15px;background:white"/>
        </div>
      </div>
    </div>

    <!-- Recurring panel -->
    <div id="panel-recurring" style="display:none;background:#f9f9f9;border-radius:12px;padding:20px;margin-bottom:24px">
      <p style="font-size:13px;color:#888;margin-bottom:14px">Sends automatically on a repeating schedule while the app is open.</p>
      <div style="display:flex;gap:12px">
        <div style="flex:1">
          <span style="font-size:12px;color:#888;display:block;margin-bottom:6px">DAY</span>
          <select id="sched-day" style="width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:15px;background:white">
            <option value="daily">Every day</option>
            <option value="monday">Every Monday</option>
            <option value="tuesday">Every Tuesday</option>
            <option value="wednesday">Every Wednesday</option>
            <option value="thursday">Every Thursday</option>
            <option value="friday" selected>Every Friday</option>
            <option value="saturday">Every Saturday</option>
            <option value="sunday">Every Sunday</option>
          </select>
        </div>
        <div style="flex:1">
          <span style="font-size:12px;color:#888;display:block;margin-bottom:6px">TIME</span>
          <input id="sched-time" type="time" value="09:30"
            style="width:100%;padding:10px 14px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:15px;background:white"/>
        </div>
      </div>
    </div>

    <button id="action-btn" style="width:100%;padding:14px;background:#1a1a1a;color:white;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;margin-bottom:24px">
      Send Now
    </button>

    <div id="status" style="padding:12px 16px;border-radius:8px;font-size:14px;display:none"></div>
  </div>
`

// Set today's date as default for the once picker
var today = new Date().toISOString().split('T')[0]
document.getElementById('once-date').value = today

// Set current time + 5 mins as default
var now = new Date()
now.setMinutes(now.getMinutes() + 5)
var hh = String(now.getHours()).padStart(2, '0')
var mm = String(now.getMinutes()).padStart(2, '0')
document.getElementById('once-time').value = hh + ':' + mm

// Tab switching
var currentMode = 'now'
document.querySelectorAll('.mode-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    currentMode = this.getAttribute('data-mode')
    document.querySelectorAll('.mode-btn').forEach(function(b) {
      b.style.background = 'white'
      b.style.color = '#444'
      b.style.borderColor = '#e0e0e0'
    })
    this.style.background = '#1a1a1a'
    this.style.color = 'white'
    this.style.borderColor = '#1a1a1a'

    document.getElementById('panel-once').style.display = currentMode === 'once' ? 'block' : 'none'
    document.getElementById('panel-recurring').style.display = currentMode === 'recurring' ? 'block' : 'none'

    var labels = { now: 'Send Now', once: 'Schedule One-Time Send', recurring: 'Set Recurring Schedule' }
    document.getElementById('action-btn').textContent = labels[currentMode]
  })
})

function getConfig() {
  return {
    sender: document.getElementById('sender').value.trim(),
    password: document.getElementById('password').value.replace(/\s/g, ''),
    recipients: document.getElementById('recipients').value.split(',').map(function(e) { return e.trim(); }).filter(function(e) { return e; }),
    subject: document.getElementById('subject').value.trim(),
    body: document.getElementById('body').value.trim(),
    schedDay: document.getElementById('sched-day').value,
    schedTime: document.getElementById('sched-time').value,
    onceDate: document.getElementById('once-date').value,
    onceTime: document.getElementById('once-time').value,
    mode: currentMode
  }
}

function showStatus(message, success) {
  var el = document.getElementById('status')
  el.style.display = 'block'
  el.style.background = success ? '#e6f4ea' : '#fce8e6'
  el.style.color = success ? '#137333' : '#c5221f'
  el.textContent = message
}

document.getElementById('action-btn').addEventListener('click', function() {
  var config = getConfig()
  if (!config.sender || !config.password || !config.recipients[0]) {
    showStatus('Please fill in sender, password, and recipients.', false)
    return
  }

  if (currentMode === 'now') {
    showStatus('Sending...', true)
    ipcRenderer.send('run-emailer', config)

  } else if (currentMode === 'once') {
    if (!config.onceDate || !config.onceTime) {
      showStatus('Please pick a date and time.', false)
      return
    }
    showStatus('Scheduled! Will send once on ' + config.onceDate + ' at ' + config.onceTime + ' then stop.', true)
    ipcRenderer.send('run-emailer', config)

  } else if (currentMode === 'recurring') {
    var sel = document.getElementById('sched-day')
    var dayLabel = sel.options[sel.selectedIndex].text
    showStatus('Recurring schedule set! Sending ' + dayLabel + ' at ' + config.schedTime + ' while app is open.', true)
    ipcRenderer.send('run-emailer', config)
  }
})

ipcRenderer.on('emailer-result', function(event, result) {
  showStatus(result.success ? 'Email sent successfully!' : 'Error: ' + result.message, result.success)
})