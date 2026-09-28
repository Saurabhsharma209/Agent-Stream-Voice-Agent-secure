// Call Analytics JS — served as static file to avoid Python f-string escaping issues

let cachedCallLogs = [];
let filteredCallLogs = [];

// ── helpers ──────────────────────────────────────────────────────────────────

function yesNoBadge(val, rgb, label) {
    if (val === 'Yes') {
        return '<span style="background:rgba(' + rgb + ',0.18);color:rgb(' + rgb + ');padding:0.2rem 0.55rem;border-radius:20px;font-size:0.72rem;font-weight:700;white-space:nowrap;">' + label + '</span>';
    }
    return '<span style="color:var(--text-muted);font-size:0.75rem;">No</span>';
}

function td(content) {
    return '<td style="padding:0.55rem 0.75rem;vertical-align:top;border-bottom:1px solid var(--border);">' + content + '</td>';
}

function muted(v) {
    return v ? v : '<span style="color:var(--text-muted)">&#8212;</span>';
}

// ── render rows ───────────────────────────────────────────────────────────────

function renderCallRows(logs) {
    var listEl = document.getElementById('calls-list');
    var countEl = document.getElementById('calls-count');

    if (!logs || logs.length === 0) {
        listEl.innerHTML = '<tr><td colspan="17" style="text-align:center;color:var(--text-muted);padding:2.5rem;">No matching call logs found. Make a call to log analytics!</td></tr>';
        if (countEl) countEl.textContent = '';
        return;
    }

    if (countEl) countEl.textContent = 'Showing ' + logs.length + ' record' + (logs.length !== 1 ? 's' : '');

    var rows = '';
    for (var i = 0; i < logs.length; i++) {
        var log = logs[i];

        // Duration
        var durLabel = log.duration || '—';
        if (typeof log.duration_seconds === 'number') {
            var s = Math.round(log.duration_seconds);
            durLabel = s >= 60 ? (Math.floor(s / 60) + 'm ' + (s % 60) + 's') : (s + 's');
        }

        // Summary truncation (no inline onclick — use data attribute)
        var summary = log.call_summary || '';
        var summaryHtml;
        if (summary.length > 120) {
            var short = summary.substring(0, 120) + '\u2026';
            summaryHtml = '<span class="summary-short">' + short +
                ' <a href="#" class="see-more-btn" style="color:var(--accent);font-size:0.72rem;">See more</a></span>' +
                '<span class="summary-full" style="display:none;">' + summary +
                ' <a href="#" class="see-less-btn" style="color:var(--accent);font-size:0.72rem;">See less</a></span>';
        } else {
            summaryHtml = summary || '<span style="color:var(--text-muted)">&#8212;</span>';
        }

        // Business interest badge
        var bizBadge = (log.business_interest && log.business_interest !== 'Not provided')
            ? '<span style="font-size:0.75rem;background:rgba(59,130,246,0.12);color:#60a5fa;padding:0.2rem 0.55rem;border-radius:20px;white-space:nowrap;">' + log.business_interest + '</span>'
            : '<span style="color:var(--text-muted)">&#8212;</span>';

        var callId = log.call_id || '';

        rows += '<tr style="transition:background 0.15s;" onmouseover="this.style.background=\'rgba(255,255,255,0.025)\'" onmouseout="this.style.background=\'\'">'
            + td('<span style="color:var(--text-muted)">' + (i + 1) + '</span>')
            + td('<span style="white-space:nowrap;font-weight:500">' + (log.call_date || '—') + '</span>')
            + td('<span style="white-space:nowrap;color:var(--text-muted)">' + (log.time || '—') + '</span>')
            + td('<span style="white-space:nowrap;">' + durLabel + '</span>')
            + td(muted(log.agent_name))
            + td(muted(log.company_name))
            + td('<code style="font-size:0.78rem;color:#93c5fd">' + (log.caller_phone_no || '—') + '</code>')
            + td('<code style="font-size:0.78rem;color:#c4b5fd">' + (log.lead_phone_no || '—') + '</code>')
            + td('<strong>' + (log.name || '—') + '</strong>')
            + td('<span style="font-size:0.78rem;max-width:130px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (log.address || '') + '">' + (log.address || '—') + '</span>')
            + td('<span style="font-size:0.78rem;max-width:140px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (log.email_id || '') + '">' + (log.email_id || '—') + '</span>')
            + td(yesNoBadge(log.caller_meeting_consent, '16,185,129', '&#10003; Yes'))
            + td(yesNoBadge(log.customer_request_raised_field_visit, '139,92,246', '&#10003; Yes'))
            + td(bizBadge)
            + td('<div style="font-size:0.78rem;max-width:220px;white-space:normal;word-wrap:break-word;line-height:1.45;">' + summaryHtml + '</div>')
            + td('<button class="btn btn-primary" style="padding:0.35rem 0.7rem;font-size:0.78rem;white-space:nowrap;" data-call-id="' + callId + '" onclick="viewTranscript(this.dataset.callId)">&#128196; View</button>')
            + td('<button class="btn btn-danger" style="padding:0.35rem 0.7rem;font-size:0.78rem;" data-call-id="' + callId + '" onclick="handleDeleteCallLog(this.dataset.callId)">&#128465;</button>')
            + '</tr>';
    }
    listEl.innerHTML = rows;

    // Bind see more / see less via event delegation
    listEl.querySelectorAll('.see-more-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            var short = this.closest('.summary-short');
            if (short) { short.style.display = 'none'; short.nextElementSibling.style.display = ''; }
        });
    });
    listEl.querySelectorAll('.see-less-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            var full = this.closest('.summary-full');
            if (full) { full.style.display = 'none'; full.previousElementSibling.style.display = ''; }
        });
    });
}

// ── load from API ─────────────────────────────────────────────────────────────

async function loadCallLogs() {
    var listEl = document.getElementById('calls-list');
    listEl.innerHTML = '<tr><td colspan="17" style="text-align:center;color:var(--text-muted);padding:2.5rem;">&#9203; Loading call logs from MongoDB…</td></tr>';
    try {
        var response = await fetch('/api/v1/calls/logs');
        if (!response.ok) throw new Error('Failed to load call logs');
        var data = await response.json();
        cachedCallLogs = data;
        filteredCallLogs = data;
        renderCallRows(data);
    } catch (err) {
        listEl.innerHTML = '<tr><td colspan="17" style="text-align:center;color:var(--error);padding:2.5rem;">&#10060; Error: ' + err.message + '</td></tr>';
    }
}

// ── search / filter ───────────────────────────────────────────────────────────

function filterCallLogs() {
    var q = (document.getElementById('calls-search').value || '').toLowerCase().trim();
    if (!q) {
        filteredCallLogs = cachedCallLogs;
    } else {
        filteredCallLogs = cachedCallLogs.filter(function(log) {
            var haystack = [log.name, log.caller_phone_no, log.lead_phone_no, log.email_id,
                log.business_interest, log.company_name, log.agent_name, log.address,
                log.call_date, log.call_summary].join(' ').toLowerCase();
            return haystack.indexOf(q) !== -1;
        });
    }
    renderCallRows(filteredCallLogs);
}

// ── CSV export ────────────────────────────────────────────────────────────────

function exportCallLogsCSV() {
    var rows = (filteredCallLogs.length > 0 ? filteredCallLogs : cachedCallLogs);
    if (rows.length === 0) { alert('No data to export.'); return; }
    var dq = '"';
    var headers = ['Sr.No.','Call Date','Time','Duration','Agent Name','Company Name',
        'Caller Phone No.','Lead Phone No.','Name','Address','Email ID',
        'Caller Meeting Consent','Field Visit Request','Business Interest','Call Summary'];
    function esc(v) { return dq + String(v == null ? '' : v).split(dq).join(dq + dq) + dq; }
    var lines = [headers.map(esc).join(',')];
    for (var i = 0; i < rows.length; i++) {
        var log = rows[i];
        lines.push([
            i + 1, log.call_date || '', log.time || '', log.duration || '',
            log.agent_name || '', log.company_name || '', log.caller_phone_no || '',
            log.lead_phone_no || '', log.name || '', log.address || '',
            log.email_id || '', log.caller_meeting_consent || '',
            log.customer_request_raised_field_visit || '', log.business_interest || '',
            log.call_summary || ''
        ].map(esc).join(','));
    }
    var blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'call_analytics_' + new Date().toISOString().split('T')[0] + '.csv';
    a.click();
    URL.revokeObjectURL(url);
}

// ── transcript modal ──────────────────────────────────────────────────────────

function viewTranscript(callId) {
    var log = null;
    for (var i = 0; i < cachedCallLogs.length; i++) {
        if (cachedCallLogs[i].call_id === callId) { log = cachedCallLogs[i]; break; }
    }
    if (!log) return;

    var modalBody = document.getElementById('modal-body');
    document.getElementById('modal-title').innerText = 'Transcript: ' + callId;

    if (!log.transcript || log.transcript.length === 0) {
        modalBody.innerHTML = '<p style="color:var(--text-muted);text-align:center;margin-top:2rem;">No transcript recorded for this call.</p>';
    } else {
        var html = '';
        for (var j = 0; j < log.transcript.length; j++) {
            var t = log.transcript[j];
            var isBot = t.role === 'bot';
            var name = isBot ? (log.agent_name || 'Bot') : 'Customer';
            var bg = isBot ? 'rgba(59,130,246,0.15)' : 'rgba(255,255,255,0.05)';
            var border = isBot ? 'rgba(59,130,246,0.3)' : 'var(--border)';
            var align = isBot ? 'flex-start' : 'flex-end';
            html += '<div style="display:flex;flex-direction:column;align-items:' + align + ';margin-bottom:1rem;">'
                + '<span style="font-size:0.75rem;color:var(--text-muted);margin-bottom:0.25rem;">' + name + '</span>'
                + '<div style="background:' + bg + ';border:1px solid ' + border + ';border-radius:12px;padding:0.75rem 1rem;max-width:80%;font-size:0.85rem;color:var(--text);word-wrap:break-word;white-space:pre-wrap;">' + (t.msg || '') + '</div>'
                + '</div>';
        }
        modalBody.innerHTML = html;
    }
    document.getElementById('transcript-modal').style.display = 'flex';
}

function closeTranscriptModal() {
    document.getElementById('transcript-modal').style.display = 'none';
}

// ── delete call log ───────────────────────────────────────────────────────────

async function handleDeleteCallLog(callId) {
    if (!confirm('Permanently delete this call log from MongoDB?')) return;
    try {
        var response = await fetch('/api/v1/calls/logs/' + callId, { method: 'DELETE' });
        if (!response.ok) throw new Error('Failed to delete');
        if (typeof showAlert === 'function') showAlert('calls-alert', 'Call log deleted successfully!');
        loadCallLogs();
    } catch (err) {
        if (typeof showAlert === 'function') showAlert('calls-alert', err.message, true);
    }
}
