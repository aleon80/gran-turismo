(function () {
    'use strict';

    var overlay = document.getElementById('overlay');
    var dashboard = document.getElementById('dashboard');
    var alertShift = document.getElementById('alert-shift');
    var alertBrake = document.getElementById('alert-brake');

    // Track info
    var trackNameEl = document.getElementById('track-name');
    var demoBtn = document.getElementById('demo-btn');
    var demoRecording = false;

    demoBtn.addEventListener('click', function () {
        if (demoRecording) {
            fetch('/api/demo/stop', { method: 'POST' });
            demoBtn.textContent = 'ЗАП ДЕМО';
            demoBtn.classList.remove('recording');
            demoRecording = false;
        } else {
            fetch('/api/demo/start', { method: 'POST' });
            demoBtn.textContent = '\u25CF ЗАПИС...';
            demoBtn.classList.add('recording');
            demoRecording = true;
        }
    });

    // Elements
    var rpmBar = document.getElementById('rpm-bar');
    var rpmVal = document.getElementById('rpm-val');
    var speedEl = document.getElementById('speed');
    var gearEl = document.getElementById('gear');
    var boostEl = document.getElementById('boost');
    var refGearEl = document.getElementById('ref-gear');
    var shiftHint = document.getElementById('shift-hint');
    var gearLimitsEl = document.getElementById('gear-limits');
    var throttleBar = document.getElementById('throttle-bar');
    var throttleVal = document.getElementById('throttle-val');
    var brakeBar = document.getElementById('brake-bar');
    var brakeVal = document.getElementById('brake-val');
    var lapInfo = document.getElementById('lap-info');
    var bestLap = document.getElementById('best-lap');
    var lastLap = document.getElementById('last-lap');
    var curTime = document.getElementById('cur-time');
    var racePos = document.getElementById('race-pos');
    var fuelBar = document.getElementById('fuel-bar');
    var fuelVal = document.getElementById('fuel-val');
    var tireFl = document.getElementById('tire-fl');
    var tireFr = document.getElementById('tire-fr');
    var tireRl = document.getElementById('tire-rl');
    var tireRr = document.getElementById('tire-rr');
    var oilTemp = document.getElementById('oil-temp');
    var waterTemp = document.getElementById('water-temp');
    var slipWarn = document.getElementById('slip-warn');
    var fuelLapsEl = document.getElementById('fuel-laps');
    var pitInfoEl = document.getElementById('pit-info');
    var refSpeedEl = document.getElementById('ref-speed');
    var brakeAheadEl = document.getElementById('brake-ahead');
    var alertPit = document.getElementById('alert-pit');
    var pitAlertTimeout = null;

    // G-force canvas
    var gfCanvas = document.getElementById('gforce');
    var gfCtx = gfCanvas.getContext('2d');
    var gHistory = []; // smoothed G trail

    // Track map
    var mapCanvas = document.getElementById('track-map');
    var mapCtx = mapCanvas.getContext('2d');
    var trail = [];         // stored trail from server
    var curPos = null;      // current car position [x, z]
    var refLine = [];       // reference racing line [[x, z], ...]

    // Speed graph
    var sgCanvas = document.getElementById('speed-graph');
    var sgCtx = sgCanvas.getContext('2d');
    var refSpeedProfile = []; // [[dist, speed], ...]

    // Throttle %
    var throttlePctEl = document.getElementById('throttle-pct');

    // Coach elements
    var coachTip = document.getElementById('coach-tip');
    var deltaTime = document.getElementById('delta-time');
    var deltaSpeed = document.getElementById('delta-speed');
    var coachStatus = document.getElementById('coach-status');
    var sectorTable = document.getElementById('sector-table');

    // Lap report elements
    var lapReport = document.getElementById('lap-report');
    var lrContent = document.getElementById('lr-content');
    var lrClose = document.getElementById('lr-close');
    var lrBadge = document.getElementById('lr-badge');
    var cachedReports = [];
    var lastReportCount = 0;
    var reportOpen = false;

    lrBadge.addEventListener('click', function () {
        reportOpen = !reportOpen;
        lapReport.classList.toggle('hidden', !reportOpen);
        lrBadge.classList.remove('has-new');
    });
    lrClose.addEventListener('click', function () {
        reportOpen = false;
        lapReport.classList.add('hidden');
    });

    function renderReports(reports) {
        if (!reports || reports.length === 0) return;

        var html = '';
        // Show newest first
        for (var r = reports.length - 1; r >= 0; r--) {
            var rpt = reports[r];
            var s = rpt.summary;
            html += '<div class="lr-lap">';
            html += '<div class="lr-lap-header">';
            html += '<span class="lr-lap-num">Коло ' + rpt.lap + '</span>';
            html += '<span class="lr-lap-time">' + formatTime(rpt.time_ms) + '</span>';
            html += '</div>';

            html += '<div class="lr-totals">';
            if (s.total_loss > 0) html += '<span class="lr-loss">-' + s.total_loss.toFixed(2) + 'с втрач.</span>';
            if (s.total_gain > 0) html += '<span class="lr-gain">+' + s.total_gain.toFixed(2) + 'с виграно</span>';
            html += '</div>';

            // Top errors
            if (s.top_errors) {
                for (var e = 0; e < s.top_errors.length; e++) {
                    var err = s.top_errors[e];
                    html += '<div class="lr-error">';
                    html += '<span class="lr-zone">' + err.zone + '</span>';
                    html += '<span class="lr-delta loss">+' + err.loss.toFixed(2) + 's</span>';
                    var typeLabels = {
                        brake_early: 'рано гальмував', brake_late: 'пізно гальмував',
                        slow_corner: 'повільний поворот', slow_exit: 'повільний вихід',
                        wrong_gear: 'неправильна передача', unnecessary_brake: 'зайве гальмування',
                        fast_corner: 'швидкий поворот'
                    };
                    html += '<span class="lr-type ' + err.type + '">' + (typeLabels[err.type] || err.type) + '</span>';
                    html += '<span class="lr-detail">' + err.detail + '</span>';
                    html += '</div>';
                }
            }

            // Improvements
            if (s.improvements && s.improvements.length > 0) {
                html += '<div class="lr-improvements">';
                for (var im = 0; im < s.improvements.length; im++) {
                    var imp = s.improvements[im];
                    html += imp.zone + ': -' + imp.gain.toFixed(2) + 's ';
                }
                html += '</div>';
            }

            html += '</div>';
        }

        lrContent.innerHTML = html;
    }

    var lastDataTime = 0;
    var connected = false;
    var lapStartTime = 0;
    var prevLap = -1;
    var tipFadeTimeout = null;
    var shiftFadeTimeout = null;
    var shiftAlertTimeout = null;
    var brakeAlertTimeout = null;
    var lastSectorCount = 0;
    var cachedAllLaps = [];

    function showShiftAlert(text, direction) {
        alertShift.textContent = text;
        alertShift.className = 'alert-overlay alert-shift ' + direction + ' visible';
        dashboard.className = 'dashboard glow-shift-' + direction;
        if (shiftAlertTimeout) clearTimeout(shiftAlertTimeout);
        shiftAlertTimeout = setTimeout(function () {
            alertShift.className = 'alert-overlay alert-shift';
            dashboard.className = 'dashboard';
        }, 1200);
    }

    function showBrakeAlert(text, type) {
        alertBrake.textContent = text;
        alertBrake.className = 'alert-overlay alert-brake ' + type + ' visible';
        dashboard.className = 'dashboard glow-brake';
        if (brakeAlertTimeout) clearTimeout(brakeAlertTimeout);
        brakeAlertTimeout = setTimeout(function () {
            alertBrake.className = 'alert-overlay alert-brake';
            if (!alertShift.classList.contains('visible')) {
                dashboard.className = 'dashboard';
            }
        }, 1500);
    }

    function formatTime(ms) {
        if (ms <= 0) return '-:--:---';
        var minutes = Math.floor(ms / 60000);
        var seconds = Math.floor((ms % 60000) / 1000);
        var millis = ms % 1000;
        return minutes + ':' +
            (seconds < 10 ? '0' : '') + seconds + '.' +
            (millis < 10 ? '00' : millis < 100 ? '0' : '') + millis;
    }

    function gearLabel(g) {
        if (g === 0) return 'R';
        if (g >= 1 && g <= 8) return String(g);
        return 'N';
    }

    function tireClass(temp) {
        if (temp < 50) return 'cold';
        if (temp < 100) return 'optimal';
        return 'hot';
    }

    function setTire(el, temp) {
        el.textContent = Math.round(temp) + '\u00B0';
        el.className = 'tire ' + tireClass(temp);
    }

    function formatSector(ms) {
        if (!ms || ms <= 0) return '-';
        var seconds = Math.floor(ms / 1000);
        var millis = ms % 1000;
        return seconds + '.' + (millis < 10 ? '00' : millis < 100 ? '0' : '') + millis;
    }

    function renderSectorTable(allLaps, curSectors) {
        if (!allLaps || allLaps.length === 0) {
            sectorTable.innerHTML = '';
            return;
        }

        // Find best sector times across all laps
        var bestS = {};
        var bestTotal = Infinity;
        var bestLapIdx = -1;
        for (var i = 0; i < allLaps.length; i++) {
            var lap = allLaps[i];
            for (var s = 1; s <= 3; s++) {
                var key = 's' + s;
                if (lap[key] && (!bestS[key] || lap[key] < bestS[key])) {
                    bestS[key] = lap[key];
                }
            }
            if (lap.total && lap.total < bestTotal) {
                bestTotal = lap.total;
                bestLapIdx = i;
            }
        }

        var html = '<table><tr><th>КОЛО</th><th>С1</th><th>С2</th><th>С3</th><th>РАЗОМ</th></tr>';

        for (var j = 0; j < allLaps.length; j++) {
            var l = allLaps[j];
            var isBest = (j === bestLapIdx);
            html += '<tr' + (isBest ? ' class="best-row"' : '') + '>';
            html += '<td>' + l.lap + '</td>';

            for (var k = 1; k <= 3; k++) {
                var sk = 's' + k;
                var val = l[sk];
                var cls = '';
                if (val && bestS[sk]) {
                    if (val <= bestS[sk]) cls = ' class="best-overall"';
                    else if (j > 0) {
                        // compare to previous lap
                        var prev = allLaps[j - 1];
                        if (prev[sk] && val < prev[sk]) cls = ' class="improved"';
                        else if (prev[sk] && val > prev[sk]) cls = ' class="slower"';
                    }
                }
                html += '<td' + cls + '>' + formatSector(val) + '</td>';
            }

            // Total
            var totalCls = '';
            if (l.total && l.total <= bestTotal) totalCls = ' class="best-overall"';
            else if (j > 0 && allLaps[j-1].total && l.total > allLaps[j-1].total) totalCls = ' class="slower"';
            else if (j > 0 && allLaps[j-1].total && l.total < allLaps[j-1].total) totalCls = ' class="improved"';
            html += '<td' + totalCls + '>' + formatTime(l.total) + '</td>';
            html += '</tr>';
        }

        // Current lap in-progress sectors
        if (curSectors && curSectors.length > 0) {
            html += '<tr><td style="color:#66ccff">зараз</td>';
            for (var m = 0; m < 3; m++) {
                if (m < curSectors.length) {
                    var cv = curSectors[m];
                    var ccls = '';
                    var bk = 's' + (m + 1);
                    if (bestS[bk] && cv <= bestS[bk]) ccls = ' class="best-overall"';
                    else if (bestS[bk] && cv > bestS[bk]) ccls = ' class="slower"';
                    html += '<td' + ccls + '>' + formatSector(cv) + '</td>';
                } else {
                    html += '<td>-</td>';
                }
            }
            html += '<td>-</td></tr>';
        }

        html += '</table>';
        sectorTable.innerHTML = html;
    }

    function posLabel(pos, total) {
        if (pos <= 0) return '-';
        var s = String(pos);
        if (total > 0) {
            return s + '/' + total;
        }
        return s;
    }

    // --- G-force drawing ---
    function drawGforce(gLat, gLon) {
        var w = gfCanvas.width;
        var h = gfCanvas.height;
        var cx = w / 2;
        var cy = h / 2;
        var maxG = 2.5;
        var scale = (w / 2 - 8) / maxG;

        gfCtx.clearRect(0, 0, w, h);

        // Grid circles
        gfCtx.strokeStyle = '#222244';
        gfCtx.lineWidth = 0.5;
        for (var r = 1; r <= 2; r++) {
            gfCtx.beginPath();
            gfCtx.arc(cx, cy, r * scale, 0, Math.PI * 2);
            gfCtx.stroke();
        }
        // Crosshair
        gfCtx.beginPath();
        gfCtx.moveTo(cx, 2); gfCtx.lineTo(cx, h - 2);
        gfCtx.moveTo(2, cy); gfCtx.lineTo(w - 2, cy);
        gfCtx.stroke();

        // Trail
        gHistory.push([gLat, gLon]);
        if (gHistory.length > 30) gHistory.shift();
        if (gHistory.length > 1) {
            gfCtx.beginPath();
            gfCtx.moveTo(cx + gHistory[0][0] * scale, cy - gHistory[0][1] * scale);
            for (var i = 1; i < gHistory.length; i++) {
                gfCtx.lineTo(cx + gHistory[i][0] * scale, cy - gHistory[i][1] * scale);
            }
            gfCtx.strokeStyle = 'rgba(0,180,255,0.3)';
            gfCtx.lineWidth = 2;
            gfCtx.stroke();
        }

        // Current dot
        var dx = Math.max(-maxG, Math.min(maxG, gLat)) * scale;
        var dy = Math.max(-maxG, Math.min(maxG, gLon)) * scale;
        var gMag = Math.sqrt(gLat * gLat + gLon * gLon);
        var dotColor = gMag > 1.5 ? '#ff4444' : gMag > 0.8 ? '#ffaa00' : '#00bbff';
        gfCtx.beginPath();
        gfCtx.arc(cx + dx, cy - dy, 4, 0, Math.PI * 2);
        gfCtx.fillStyle = dotColor;
        gfCtx.fill();

        // G value text
        gfCtx.fillStyle = '#666';
        gfCtx.font = '9px sans-serif';
        gfCtx.textAlign = 'center';
        gfCtx.fillText(gMag.toFixed(1) + 'G', cx, h - 3);
    }

    // Speed to color (blue -> green -> yellow -> red)
    function speedColor(speed, maxSpeed) {
        var t = Math.min(speed / (maxSpeed || 300), 1);
        if (t < 0.33) {
            var p = t / 0.33;
            return 'rgb(' + Math.round(0) + ',' + Math.round(100 + 155 * p) + ',' + Math.round(255 * (1 - p)) + ')';
        } else if (t < 0.66) {
            var p2 = (t - 0.33) / 0.33;
            return 'rgb(' + Math.round(255 * p2) + ',' + Math.round(255) + ',0)';
        } else {
            var p3 = (t - 0.66) / 0.34;
            return 'rgb(255,' + Math.round(255 * (1 - p3)) + ',0)';
        }
    }

    // Delta speed to color (red = slower, green = faster)
    function deltaColor(delta) {
        var clamped = Math.max(-20, Math.min(20, delta));
        if (clamped >= 0) {
            var t = clamped / 20;
            return 'rgb(' + Math.round(100 * (1 - t)) + ',' + Math.round(150 + 105 * t) + ',' + Math.round(100 * (1 - t)) + ')';
        } else {
            var t2 = -clamped / 20;
            return 'rgb(' + Math.round(150 + 105 * t2) + ',' + Math.round(100 * (1 - t2)) + ',' + Math.round(80 * (1 - t2)) + ')';
        }
    }

    // --- Track map drawing ---
    function drawTrackMap() {
        var w = mapCanvas.width;
        var h = mapCanvas.height;
        mapCtx.clearRect(0, 0, w, h);

        if (trail.length < 2) return;

        // Find bounds (include refLine for alignment)
        var minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
        var allPts = trail;
        if (refLine.length > 0) {
            allPts = trail.concat(refLine.map(function(p) { return p; }));
        }
        for (var i = 0; i < allPts.length; i++) {
            var p = allPts[i];
            if (p[0] < minX) minX = p[0];
            if (p[0] > maxX) maxX = p[0];
            if (p[1] < minZ) minZ = p[1];
            if (p[1] > maxZ) maxZ = p[1];
        }

        var rangeX = maxX - minX || 1;
        var rangeZ = maxZ - minZ || 1;
        var pad = 15;
        var drawW = w - pad * 2;
        var drawH = h - pad * 2;

        // Keep aspect ratio
        var scale = Math.min(drawW / rangeX, drawH / rangeZ);
        var offX = pad + (drawW - rangeX * scale) / 2;
        var offZ = pad + (drawH - rangeZ * scale) / 2;

        function tx(x) { return offX + (x - minX) * scale; }
        function tz(z) { return offZ + (z - minZ) * scale; }

        // Find max speed for color scale
        var maxSpd = 100;
        for (var si = 0; si < trail.length; si++) {
            if (trail[si][2] && trail[si][2] > maxSpd) maxSpd = trail[si][2];
        }

        // Draw reference racing line (thin, dashed)
        if (refLine.length > 1) {
            mapCtx.beginPath();
            mapCtx.moveTo(tx(refLine[0][0]), tz(refLine[0][1]));
            for (var ri = 1; ri < refLine.length; ri++) {
                mapCtx.lineTo(tx(refLine[ri][0]), tz(refLine[ri][1]));
            }
            mapCtx.strokeStyle = 'rgba(100, 180, 255, 0.3)';
            mapCtx.lineWidth = 1.5;
            mapCtx.setLineDash([4, 4]);
            mapCtx.stroke();
            mapCtx.setLineDash([]);
        }

        // Draw trail colored by delta speed (if available) or absolute speed
        var hasDelta = trail.length > 0 && trail[0].length > 3;
        mapCtx.lineWidth = 3;
        mapCtx.lineJoin = 'round';
        mapCtx.lineCap = 'round';
        for (var j = 1; j < trail.length; j++) {
            mapCtx.beginPath();
            mapCtx.moveTo(tx(trail[j-1][0]), tz(trail[j-1][1]));
            mapCtx.lineTo(tx(trail[j][0]), tz(trail[j][1]));
            if (hasDelta && trail[j][3] !== 0) {
                mapCtx.strokeStyle = deltaColor(trail[j][3]);
            } else {
                var spd = trail[j][2] || 0;
                mapCtx.strokeStyle = spd > 0 ? speedColor(spd, maxSpd) : '#334466';
            }
            mapCtx.stroke();
        }

        // Draw error zones from last lap report
        if (cachedReports.length > 0) {
            var lastReport = cachedReports[cachedReports.length - 1];
            var errs = lastReport.summary ? lastReport.summary.top_errors : [];
            for (var ei = 0; ei < errs.length; ei++) {
                var ez = errs[ei];
                if (ez.x && ez.z) {
                    var ex = tx(ez.x);
                    var ey = tz(ez.z);
                    // Red pulsing circle for problem zones
                    mapCtx.beginPath();
                    mapCtx.arc(ex, ey, 8, 0, Math.PI * 2);
                    mapCtx.fillStyle = 'rgba(255, 50, 50, 0.3)';
                    mapCtx.fill();
                    mapCtx.beginPath();
                    mapCtx.arc(ex, ey, 4, 0, Math.PI * 2);
                    mapCtx.fillStyle = 'rgba(255, 80, 80, 0.7)';
                    mapCtx.fill();
                    // Label
                    mapCtx.fillStyle = '#ff8888';
                    mapCtx.font = '7px sans-serif';
                    mapCtx.textAlign = 'center';
                    mapCtx.fillText(ez.zone, ex, ey - 10);
                }
            }
        }

        // Draw car position
        if (curPos) {
            var cx = tx(curPos[0]);
            var cz = tz(curPos[1]);
            mapCtx.beginPath();
            mapCtx.arc(cx, cz, 5, 0, Math.PI * 2);
            mapCtx.fillStyle = '#00ff88';
            mapCtx.fill();
            // Glow
            mapCtx.beginPath();
            mapCtx.arc(cx, cz, 8, 0, Math.PI * 2);
            mapCtx.strokeStyle = 'rgba(0, 255, 136, 0.4)';
            mapCtx.lineWidth = 2;
            mapCtx.stroke();
        }
    }

    // --- Speed graph ---
    function drawSpeedGraph() {
        var w = sgCanvas.width;
        var h = sgCanvas.height;
        sgCtx.clearRect(0, 0, w, h);

        if (refSpeedProfile.length < 2 && trail.length < 2) return;

        // Find max distance and max speed
        var maxDist = 0;
        var maxSpd = 100;
        for (var i = 0; i < refSpeedProfile.length; i++) {
            if (refSpeedProfile[i][0] > maxDist) maxDist = refSpeedProfile[i][0];
            if (refSpeedProfile[i][1] > maxSpd) maxSpd = refSpeedProfile[i][1];
        }

        // Build current lap speed profile from trail
        var curProfile = [];
        if (trail.length > 1) {
            var cumDist = 0;
            curProfile.push([0, trail[0][2] || 0]);
            for (var ti = 1; ti < trail.length; ti++) {
                var dx = trail[ti][0] - trail[ti-1][0];
                var dz = trail[ti][1] - trail[ti-1][1];
                cumDist += Math.sqrt(dx * dx + dz * dz);
                var s = trail[ti][2] || 0;
                curProfile.push([cumDist, s]);
                if (s > maxSpd) maxSpd = s;
                if (cumDist > maxDist) maxDist = cumDist;
            }
        }

        if (maxDist < 100) return;
        maxSpd = maxSpd * 1.1;

        var pad = 2;
        var drawW = w - pad * 2;
        var drawH = h - pad * 2;

        function px(dist) { return pad + (dist / maxDist) * drawW; }
        function py(spd) { return pad + drawH - (spd / maxSpd) * drawH; }

        // Grid lines
        sgCtx.strokeStyle = '#1a1a2e';
        sgCtx.lineWidth = 0.5;
        for (var g = 50; g < maxSpd; g += 50) {
            var gy = py(g);
            sgCtx.beginPath();
            sgCtx.moveTo(pad, gy);
            sgCtx.lineTo(w - pad, gy);
            sgCtx.stroke();
        }

        // Draw reference (gray)
        if (refSpeedProfile.length > 1) {
            sgCtx.beginPath();
            sgCtx.moveTo(px(refSpeedProfile[0][0]), py(refSpeedProfile[0][1]));
            for (var ri = 1; ri < refSpeedProfile.length; ri++) {
                sgCtx.lineTo(px(refSpeedProfile[ri][0]), py(refSpeedProfile[ri][1]));
            }
            sgCtx.strokeStyle = 'rgba(100, 180, 255, 0.5)';
            sgCtx.lineWidth = 1;
            sgCtx.stroke();
        }

        // Draw current (colored by delta)
        if (curProfile.length > 1) {
            sgCtx.lineWidth = 1.5;
            sgCtx.lineJoin = 'round';
            for (var ci = 1; ci < curProfile.length; ci++) {
                sgCtx.beginPath();
                sgCtx.moveTo(px(curProfile[ci-1][0]), py(curProfile[ci-1][1]));
                sgCtx.lineTo(px(curProfile[ci][0]), py(curProfile[ci][1]));
                // Find closest ref speed for coloring
                var refSpd = 0;
                for (var rs = 0; rs < refSpeedProfile.length - 1; rs++) {
                    if (refSpeedProfile[rs][0] <= curProfile[ci][0] && refSpeedProfile[rs+1][0] > curProfile[ci][0]) {
                        refSpd = refSpeedProfile[rs][1];
                        break;
                    }
                }
                if (refSpd > 0) {
                    sgCtx.strokeStyle = deltaColor(curProfile[ci][1] - refSpd);
                } else {
                    sgCtx.strokeStyle = '#00ff88';
                }
                sgCtx.stroke();
            }
        }

        // Labels
        sgCtx.fillStyle = '#555';
        sgCtx.font = '8px sans-serif';
        sgCtx.textAlign = 'left';
        sgCtx.fillText(Math.round(maxSpd * 0.9) + '', pad + 1, pad + 10);
        sgCtx.fillText('0', pad + 1, h - pad - 2);
    }

    function update(d) {
        lastDataTime = Date.now();

        // Show dashboard
        if (!connected) {
            connected = true;
            overlay.classList.add('hidden');
            dashboard.classList.remove('hidden');
        }

        // RPM
        var rpmMax = d.rpm_max || 9000;
        var rpmPct = Math.min(d.rpm / rpmMax * 100, 100);
        rpmBar.style.width = rpmPct + '%';
        rpmVal.textContent = d.rpm;

        if (d.rpm_alert > 0 && d.rpm >= d.rpm_alert) {
            rpmBar.classList.add('redline');
        } else {
            rpmBar.classList.remove('redline');
        }

        // Speed
        speedEl.textContent = Math.round(d.speed);

        // Gear
        gearEl.textContent = gearLabel(d.gear);

        // Boost
        if (d.turbo > 0) {
            boostEl.textContent = 'НАДДУВ ' + d.turbo.toFixed(2);
        } else {
            boostEl.textContent = '';
        }

        // Pedals
        throttleBar.style.height = (d.throttle * 100) + '%';
        throttleVal.textContent = Math.round(d.throttle * 100) + '%';
        brakeBar.style.height = (d.brake * 100) + '%';
        brakeVal.textContent = Math.round(d.brake * 100) + '%';

        // Race position
        racePos.textContent = posLabel(d.race_pos, d.num_cars);

        // Lap
        if (d.total_laps > 0) {
            lapInfo.textContent = d.lap + '/' + d.total_laps;
        } else {
            lapInfo.textContent = d.lap > 0 ? String(d.lap) : '-';
        }

        // Current lap time: estimate from track_time and last_lap
        // track_time is cumulative; approximate current lap time
        if (d.lap !== prevLap) {
            lapStartTime = d.track_time;
            prevLap = d.lap;
        }
        var currentLapMs = d.track_time - lapStartTime;
        if (currentLapMs > 0) {
            curTime.textContent = formatTime(currentLapMs);
        } else {
            curTime.textContent = formatTime(d.track_time);
        }

        bestLap.textContent = formatTime(d.best_lap);
        lastLap.textContent = formatTime(d.last_lap);

        // Fuel
        var fuelPct = Math.max(0, Math.min(100, d.fuel));
        fuelBar.style.width = fuelPct + '%';
        fuelVal.textContent = fuelPct.toFixed(0) + '%';
        if (fuelPct < 15) {
            fuelBar.classList.add('low');
        } else {
            fuelBar.classList.remove('low');
        }

        // Tires
        setTire(tireFl, d.tire_fl);
        setTire(tireFr, d.tire_fr);
        setTire(tireRl, d.tire_rl);
        setTire(tireRr, d.tire_rr);

        // Temps
        oilTemp.textContent = Math.round(d.oil_temp);
        waterTemp.textContent = Math.round(d.water_temp);

        // Tire slip warnings
        if (d.lockup) {
            slipWarn.textContent = 'БЛОКУВАННЯ!';
            slipWarn.className = 'slip-warn lockup';
        } else if (d.wheelspin) {
            slipWarn.textContent = 'ПРОБУКСОВКА!';
            slipWarn.className = 'slip-warn wheelspin';
        } else {
            slipWarn.textContent = '';
            slipWarn.className = 'slip-warn';
        }

        // Fuel prediction
        if (d.fuel_laps && d.fuel_laps > 0) {
            var fl = d.fuel_laps;
            fuelLapsEl.textContent = fl.toFixed(1) + ' кіл залиш.';
            fuelLapsEl.className = fl < 3 ? 'fuel-laps critical' : 'fuel-laps';
        } else {
            fuelLapsEl.textContent = '';
        }

        // G-force
        drawGforce(d.g_lat || 0, d.g_lon || 0);

        // Coach
        if (d.coach) {
            var c = d.coach;

            // Reference gear
            if (c.has_reference && c.ref_gear && c.ref_gear > 0) {
                refGearEl.textContent = '\u2192' + gearLabel(c.ref_gear);
                refGearEl.className = 'ref-gear' + (c.ref_gear !== d.gear ? ' mismatch' : '');
            } else {
                refGearEl.textContent = '';
                refGearEl.className = 'ref-gear';
            }

            // Shift hint + big alert
            if (c.shift_tip) {
                shiftHint.textContent = c.shift_tip;
                shiftHint.className = 'shift-hint ' + (c.shift_type || '');
                if (shiftFadeTimeout) clearTimeout(shiftFadeTimeout);
                shiftFadeTimeout = setTimeout(function () {
                    shiftHint.textContent = '';
                    shiftHint.className = 'shift-hint';
                }, 2000);

                // Big overlay for immediate shifts
                if (c.shift_type === 'up_now') {
                    showShiftAlert(c.shift_tip, 'up');
                } else if (c.shift_type === 'down_now') {
                    showShiftAlert(c.shift_tip, 'down');
                }
            }

            // Gear limits display
            if (c.gear_limits) {
                var glHtml = '';
                var keys = Object.keys(c.gear_limits).sort();
                for (var gi = 0; gi < keys.length; gi++) {
                    var gNum = parseInt(keys[gi]);
                    var gMax = c.gear_limits[keys[gi]];
                    var cls = 'gl-item';
                    if (gNum === d.gear) {
                        if (d.speed >= gMax * 0.97) cls += ' at-limit';
                        else cls += ' active';
                    }
                    glHtml += '<span class="' + cls + '">' + gNum + ':' + Math.round(gMax) + '</span>';
                }
                gearLimitsEl.innerHTML = glHtml;
            }

            // Tip + big brake alerts
            if (c.tip) {
                coachTip.textContent = c.tip;
                coachTip.className = 'coach-tip ' + (c.tip_type || '');
                if (tipFadeTimeout) clearTimeout(tipFadeTimeout);
                tipFadeTimeout = setTimeout(function () {
                    coachTip.textContent = '';
                }, 2500);

                // Big brake overlay
                if (c.tip_type === 'brake_urgent') {
                    showBrakeAlert('ГАЛЬМУЙ ЗАРАЗ!', 'warn');
                } else if (c.tip_type === 'brake') {
                    showBrakeAlert('ГАЛЬМУЙ ПІЗНІШЕ', 'early');
                }
            }

            // Delta time
            if (c.has_reference && c.delta_time !== 0) {
                var dt = c.delta_time;
                var sign = dt >= 0 ? '+' : '';
                deltaTime.textContent = sign + dt.toFixed(2) + 's';
                deltaTime.className = 'delta-value ' + (dt >= 0 ? 'ahead' : 'behind');
            } else {
                deltaTime.textContent = '-';
                deltaTime.className = 'delta-value';
            }

            // Delta speed
            if (c.has_reference && c.ref_speed > 0) {
                var ds = c.delta_speed;
                var sSign = ds >= 0 ? '+' : '';
                deltaSpeed.textContent = sSign + Math.round(ds) + ' km/h';
                deltaSpeed.className = 'delta-value ' + (ds >= 0 ? 'ahead' : 'behind');
            } else {
                deltaSpeed.textContent = '-';
                deltaSpeed.className = 'delta-value';
            }

            // Reference speed
            if (c.has_reference && c.ref_speed > 0) {
                var spdClass = 'match';
                if (c.delta_speed > 5) spdClass = 'faster';
                else if (c.delta_speed < -5) spdClass = 'slower';
                refSpeedEl.innerHTML = '<span class="ref-spd-val ' + spdClass + '">' +
                    Math.round(c.ref_speed) + '</span><span class="ref-spd-label">км/г етал.</span>';
            } else {
                refSpeedEl.innerHTML = '';
            }

            // Brake ahead
            if (c.brake_ahead) {
                var ba = c.brake_ahead;
                var imminent = ba.dist < 50 ? ' imminent' : '';
                if (ba.dist < 30) {
                    brakeAheadEl.innerHTML = '<span class="brake-target">ГАЛЬМУЙ до ' + ba.target_speed + '</span>';
                } else {
                    brakeAheadEl.innerHTML = '<span class="brake-dist">ГАЛЬМО ' + ba.dist + 'м</span> \u2192 <span class="brake-target">' + ba.target_speed + ' км/г</span>';
                }
                brakeAheadEl.className = 'brake-ahead' + imminent;
            } else {
                brakeAheadEl.innerHTML = '';
                brakeAheadEl.className = 'brake-ahead';
            }

            // Track detection
            if (c.track && c.track.name) {
                trackNameEl.textContent = c.track.name;
                trackNameEl.className = 'track-name detected';
                demoBtn.classList.remove('hidden');
            }

            // Demo recording state from server
            if (c.recording_demo && !demoRecording) {
                demoBtn.textContent = '\u25CF ЗАПИС...';
                demoBtn.classList.add('recording');
                demoRecording = true;
            } else if (!c.recording_demo && demoRecording) {
                demoBtn.textContent = 'ЗАП ДЕМО';
                demoBtn.classList.remove('recording');
                demoRecording = false;
            }

            // Status
            if (!c.has_reference) {
                coachStatus.textContent = 'Запис еталонного кола...';
            } else {
                coachStatus.textContent = 'Порівняння з найкращим колом';
            }

            // Cache reference data when received
            if (c.ref_line) refLine = c.ref_line;
            if (c.ref_speed_profile) refSpeedProfile = c.ref_speed_profile;

            // Throttle %
            if (c.cur_throttle_pct > 0) {
                var curPct = c.cur_throttle_pct;
                var refPct = c.ref_throttle_pct;
                if (refPct > 0) {
                    var tCls = curPct >= refPct ? 'ahead' : 'behind';
                    throttlePctEl.textContent = curPct.toFixed(0) + '% / ' + refPct.toFixed(0) + '%';
                    throttlePctEl.className = 'delta-value ' + tCls;
                } else {
                    throttlePctEl.textContent = curPct.toFixed(0) + '%';
                    throttlePctEl.className = 'delta-value';
                }
            }

            // Trail braking tip
            if (c.trail_brake_tip) {
                var tbType = c.trail_brake_tip.indexOf('ДОБРЕ') >= 0 ? 'trail_good' : 'trail_bad';
                coachTip.textContent = c.trail_brake_tip;
                coachTip.className = 'coach-tip ' + tbType;
                if (tipFadeTimeout) clearTimeout(tipFadeTimeout);
                tipFadeTimeout = setTimeout(function () {
                    coachTip.textContent = '';
                }, 2500);
            }

            // Cache all_laps when present
            if (c.all_laps) cachedAllLaps = c.all_laps;

            // Lap reports
            if (c.lap_reports && c.lap_reports.length > 0) {
                cachedReports = c.lap_reports;
                if (c.lap_reports.length > lastReportCount) {
                    lastReportCount = c.lap_reports.length;
                    lrBadge.classList.remove('hidden');
                    lrBadge.classList.add('has-new');
                    renderReports(cachedReports);
                    // Auto-open for first report
                    if (lastReportCount === 1 && !reportOpen) {
                        reportOpen = true;
                        lapReport.classList.remove('hidden');
                    }
                }
            }

            // Sector table (update when data changes)
            var totalSectors = cachedAllLaps.length * 10 + (c.cur_sectors ? c.cur_sectors.length : 0);
            if (totalSectors !== lastSectorCount) {
                lastSectorCount = totalSectors;
                renderSectorTable(cachedAllLaps, c.cur_sectors);
            }
        }

        // Pit info (center column)
        if (d.coach && d.coach.pit) {
            var pit = d.coach.pit;

            if (pit.fuel_per_lap > 0 && pit.pit_lap > 0) {
                var lapsUntilPit = pit.pit_lap - d.lap;
                pitInfoEl.innerHTML = 'ПІТ КОЛО <span class="pit-lap-num">' + pit.pit_lap + '</span>';

                // Show alert when on the pit lap or 1 lap before
                if (lapsUntilPit <= 1 && lapsUntilPit >= 0) {
                    if (!alertPit.classList.contains('visible')) {
                        alertPit.innerHTML = '<div class="pit-triangle">\u26A0\uFE0F</div><div class="pit-alert-text">БОКС БОКС БОКС</div>';
                        alertPit.classList.add('visible');
                        dashboard.className = 'dashboard glow-pit';
                        if (pitAlertTimeout) clearTimeout(pitAlertTimeout);
                        pitAlertTimeout = setTimeout(function () {
                            alertPit.classList.remove('visible');
                            if (!alertShift.classList.contains('visible') && !alertBrake.classList.contains('visible')) {
                                dashboard.className = 'dashboard';
                            }
                        }, 5000);
                    }
                }
            } else if (pit.fuel_per_lap > 0) {
                pitInfoEl.innerHTML = '<span class="pit-ok">піт не потрібен</span>';
            } else {
                pitInfoEl.innerHTML = '';
            }
        }

        // Track map data
        if (d.trail) {
            trail = d.trail;
        }
        curPos = [d.pos_x, d.pos_z];
        drawTrackMap();
        drawSpeedGraph();
    }

    // Check for stale data
    setInterval(function () {
        if (connected && Date.now() - lastDataTime > 3000) {
            connected = false;
            overlay.classList.remove('hidden');
            dashboard.classList.add('hidden');
        }
    }, 1000);

    // WebSocket connection
    function connect() {
        var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        var ws = new WebSocket(protocol + '//' + location.host + '/ws');

        ws.onmessage = function (evt) {
            try {
                var data = JSON.parse(evt.data);
                update(data);
            } catch (e) {
                // ignore parse errors
            }
        };

        ws.onclose = function () {
            setTimeout(connect, 1000);
        };

        ws.onerror = function () {
            ws.close();
        };
    }

    connect();
})();
