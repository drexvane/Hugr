/**
 * Hugr Constellation Studio & Balanced Spatial Grid Controller
 * Recreates the exact reference design with celestial star constellation,
 * slow staggered card entrances, and verified DuckDB numbers.
 */

document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const initialView = document.getElementById('initial-view');
    const studioView = document.getElementById('studio-view');
    const studioLoader = document.getElementById('studio-loader');

    // Header Elements
    const brandHome = document.getElementById('brand-home');
    const btnNewReset = document.getElementById('btn-new-reset');
    const queryPillContainer = document.getElementById('query-pill-container');
    const queryPillBubble = document.getElementById('query-pill-bubble');
    const activeQueryText = document.getElementById('active-query-text');
    const headerQueryForm = document.getElementById('header-query-form');
    const headerQueryInput = document.getElementById('header-query-input');
    const btnCloseHeaderQuery = document.getElementById('btn-close-header-query');

    const datasetPill = document.getElementById('dataset-pill');
    const headerDatasetName = document.getElementById('header-dataset-name');
    const headerDatasetMeta = document.getElementById('header-dataset-meta');
    const btnExportTrigger = document.getElementById('btn-export-trigger');
    const exportDropdownMenu = document.getElementById('export-dropdown-menu');

    // Initial View Elements
    const heroSearchForm = document.getElementById('hero-search-form');
    const heroSearchInput = document.getElementById('hero-search-input');
    const uploadCapsuleStrip = document.getElementById('upload-capsule-strip');
    const heroFilePicker = document.getElementById('hero-file-picker');
    const starterPillsList = document.getElementById('starter-pills-list');

    // Studio View Elements (Matching Reference Design)
    // Row 1: Bar Chart + KPI Cluster + Donut Chart
    const barChartTitle = document.getElementById('bar-chart-title');
    const plotBarChart = document.getElementById('plot-bar-chart');

    const kpiPrimaryLabel = document.getElementById('kpi-primary-label');
    const kpiPrimaryValue = document.getElementById('kpi-primary-value');
    const kpiComparisonText = document.getElementById('kpi-comparison-text');
    const kpiSub1Label = document.getElementById('kpi-sub1-label');
    const kpiSub1Value = document.getElementById('kpi-sub1-value');
    const kpiSub1Note = document.getElementById('kpi-sub1-note');
    const kpiSub2Label = document.getElementById('kpi-sub2-label');
    const kpiSub2Value = document.getElementById('kpi-sub2-value');
    const kpiSub2Note = document.getElementById('kpi-sub2-note');

    const donutChartTitle = document.getElementById('donut-chart-title');
    const plotDonutChart = document.getElementById('plot-donut-chart');

    // Row 2: Trend Spline + Ranking Table + Key Takeaways
    const trendChartTitle = document.getElementById('trend-chart-title');
    const plotTrendChart = document.getElementById('plot-trend-chart');

    const rankingTableTitle = document.getElementById('ranking-table-title');
    const rankingTableBody = document.getElementById('ranking-table-body');

    const takeawaysList = document.getElementById('takeaways-list');
    const btnCardFollowup = document.getElementById('btn-card-followup');

    // Bottom Bar & Drawer
    const btnOpenTableDrawer = document.getElementById('btn-open-table-drawer');
    const bottomFollowupChips = document.getElementById('bottom-followup-chips');
    const auditDrawerOverlay = document.getElementById('audit-drawer-overlay');
    const btnCloseAudit = document.getElementById('btn-close-audit');
    const auditTableHead = document.getElementById('audit-table-head');
    const auditTableBody = document.getElementById('audit-table-body');
    const auditPlanJson = document.getElementById('audit-plan-json');
    const auditRowsBadge = document.getElementById('audit-rows-badge');

    const telemetryDrawerOverlay = document.getElementById('telemetry-drawer-overlay');
    const btnCloseTelemetry = document.getElementById('btn-close-telemetry');
    const telemetryScoreBadge = document.getElementById('telemetry-score-badge');
    const telemetryMeasuresContainer = document.getElementById('telemetry-measures-container');
    const telemetryDimensionsContainer = document.getElementById('telemetry-dimensions-container');

    let currentQuestion = '';

    // =========================================================================
    // 1. SOPHISTICATED INTERACTIVE CONSTELLATION & ELECTRO/SCAN-FIELD SYSTEM
    //    Sparse, technical star points with 3D parallax, smooth orbital attraction,
    //    and a calm localized electro/scan-field aura following cursor with zero lasers.
    // =========================================================================
    const canvas = document.getElementById('constellation-canvas');
    const ctx = canvas.getContext('2d');
    let width = (canvas.width = window.innerWidth);
    let height = (canvas.height = window.innerHeight);

    function resizeCanvas() {
        width = canvas.width = window.innerWidth;
        height = canvas.height = window.innerHeight;
        triggerChartResize();
    }
    window.addEventListener('resize', resizeCanvas);

    const NUM_STARS = 52;
    const stars = [];
    const mouse = { x: -1000, y: -1000, active: false };
    const scanField = { x: -1000, y: -1000, active: false, angle: 0 };

    window.addEventListener('mousemove', (e) => {
        mouse.x = e.clientX;
        mouse.y = e.clientY;
        mouse.active = true;
    });

    window.addEventListener('mouseleave', () => {
        mouse.active = false;
    });

    for (let i = 0; i < NUM_STARS; i++) {
        const ox = Math.random() * width;
        const oy = Math.random() * height;
        const z = Math.random() * 0.9 + 0.45; // 3D depth layer: 0.45 (far) to 1.35 (near)
        stars.push({
            ox: ox,
            oy: oy,
            x: ox,
            y: oy,
            z: z,
            radius: (Math.random() * 1.0 + 0.9) * z,
            baseAlpha: (Math.random() * 0.35 + 0.18) * Math.min(z, 1.0),
            phase: Math.random() * Math.PI * 2,
            twinkleSpeed: Math.random() * 0.015 + 0.008,
            vx: 0,
            vy: 0,
            driftAngle: Math.random() * Math.PI * 2,
            driftSpeed: (Math.random() * 0.08 + 0.04) * z,
            excitation: 0.0
        });
    }

    function renderConstellation() {
        ctx.clearRect(0, 0, width, height);

        // Interpolate scan-field position smoothly toward mouse
        if (mouse.active) {
            scanField.x += (mouse.x - scanField.x) * 0.12;
            scanField.y += (mouse.y - scanField.y) * 0.12;
            scanField.active = true;
        } else {
            scanField.active = false;
        }

        scanField.angle += 0.008;

        // 1. Draw Subtle Electro/Scan-Field Aura around Cursor
        if (scanField.active && scanField.x > 0 && scanField.y > 0) {
            const auraRadius = 160;
            const auraGrad = ctx.createRadialGradient(
                scanField.x, scanField.y, 0,
                scanField.x, scanField.y, auraRadius
            );
            auraGrad.addColorStop(0, 'rgba(99, 102, 241, 0.045)');
            auraGrad.addColorStop(0.45, 'rgba(59, 130, 246, 0.018)');
            auraGrad.addColorStop(1, 'rgba(255, 255, 255, 0)');

            ctx.beginPath();
            ctx.arc(scanField.x, scanField.y, auraRadius, 0, Math.PI * 2);
            ctx.fillStyle = auraGrad;
            ctx.fill();

            // Refined Concentric Scan Reticle Arcs (Scientific & Technical, not laser-heavy)
            ctx.save();
            ctx.beginPath();
            ctx.arc(scanField.x, scanField.y, 52, scanField.angle, scanField.angle + 1.2);
            ctx.strokeStyle = 'rgba(99, 102, 241, 0.18)';
            ctx.lineWidth = 0.75;
            ctx.stroke();

            ctx.beginPath();
            ctx.arc(scanField.x, scanField.y, 96, -scanField.angle * 0.7, -scanField.angle * 0.7 + 0.8);
            ctx.strokeStyle = 'rgba(148, 163, 184, 0.12)';
            ctx.lineWidth = 0.75;
            ctx.stroke();

            // Delicate Center Reticle Ticks
            const tick = 4;
            ctx.beginPath();
            ctx.moveTo(scanField.x - tick, scanField.y);
            ctx.lineTo(scanField.x + tick, scanField.y);
            ctx.moveTo(scanField.x, scanField.y - tick);
            ctx.lineTo(scanField.x, scanField.y + tick);
            ctx.strokeStyle = 'rgba(99, 102, 241, 0.35)';
            ctx.lineWidth = 0.75;
            ctx.stroke();
            ctx.restore();
        }

        // 2. Physics Update: Drift, 3D Parallax, Smooth Orbital Gravitation
        const centerX = width / 2;
        const centerY = height / 2;

        for (let i = 0; i < stars.length; i++) {
            const s = stars[i];

            // Slow natural cosmic drift around anchor
            s.driftAngle += 0.003;
            const targetAnchorX = s.ox + Math.cos(s.driftAngle) * 14 * s.z;
            const targetAnchorY = s.oy + Math.sin(s.driftAngle) * 14 * s.z;

            // 3D Parallax displacement based on cursor position relative to screen center
            let parallaxX = 0;
            let parallaxY = 0;
            if (scanField.active) {
                parallaxX = (scanField.x - centerX) * 0.022 * (s.z - 0.75);
                parallaxY = (scanField.y - centerY) * 0.022 * (s.z - 0.75);
            }

            const targetX = targetAnchorX + parallaxX;
            const targetY = targetAnchorY + parallaxY;

            // Gravitational pull & orbital tendency toward scanField cursor
            if (scanField.active) {
                const dx = scanField.x - s.x;
                const dy = scanField.y - s.y;
                const dist = Math.hypot(dx, dy);

                if (dist < 220) {
                    const pull = (1 - dist / 220) * 0.038 * s.z;
                    s.vx += dx * pull * 0.05;
                    s.vy += dy * pull * 0.05;

                    // Excite energy field for passing particles
                    const exciteFactor = (1 - dist / 220);
                    s.excitation = Math.max(s.excitation, exciteFactor);
                }
            }

            // Spring return to parallax target & velocity damping
            s.vx += (targetX - s.x) * 0.028;
            s.vy += (targetY - s.y) * 0.028;
            s.vx *= 0.88;
            s.vy *= 0.88;

            s.x += s.vx;
            s.y += s.vy;

            // Smoothly decay excitation
            s.excitation *= 0.94;
            s.phase += s.twinkleSpeed;
        }

        // 3. Connect Nearby Stars with Fine, Technical Constellation Lines
        for (let i = 0; i < stars.length; i++) {
            const s1 = stars[i];

            for (let j = i + 1; j < stars.length; j++) {
                const s2 = stars[j];
                const d = Math.hypot(s1.x - s2.x, s1.y - s2.y);
                const maxDist = 125;

                if (d < maxDist) {
                    const baseDistAlpha = (1 - d / maxDist);
                    const combinedExcitation = Math.max(s1.excitation, s2.excitation);

                    ctx.beginPath();
                    ctx.moveTo(s1.x, s1.y);
                    ctx.lineTo(s2.x, s2.y);

                    if (combinedExcitation > 0.08) {
                        // Gently illuminates in refined indigo when energized by scan-field
                        ctx.strokeStyle = `rgba(79, 70, 229, ${0.12 + 0.32 * combinedExcitation})`;
                        ctx.lineWidth = 0.85;
                    } else {
                        // Ultra-clean faint architectural hairline
                        ctx.strokeStyle = `rgba(148, 163, 184, ${baseDistAlpha * 0.16})`;
                        ctx.lineWidth = 0.65;
                    }
                    ctx.stroke();
                }
            }

            // Connect star to cursor if inside proximity field
            if (scanField.active) {
                const distToCursor = Math.hypot(s1.x - scanField.x, s1.y - scanField.y);
                if (distToCursor < 140) {
                    const lineAlpha = (1 - distToCursor / 140) * 0.22;
                    ctx.beginPath();
                    ctx.moveTo(s1.x, s1.y);
                    ctx.lineTo(scanField.x, scanField.y);
                    ctx.strokeStyle = `rgba(79, 70, 229, ${lineAlpha})`;
                    ctx.lineWidth = 0.75;
                    ctx.stroke();
                }
            }
        }

        // 4. Render Star Points
        for (let i = 0; i < stars.length; i++) {
            const s = stars[i];
            const twinkle = Math.sin(s.phase) * 0.15;
            const alpha = Math.max(0.12, Math.min(0.85, s.baseAlpha + twinkle + s.excitation * 0.45));
            const currentRadius = s.radius + s.excitation * 1.5;

            // Excited micro-halo
            if (s.excitation > 0.12) {
                ctx.beginPath();
                ctx.arc(s.x, s.y, currentRadius + 3.0, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(99, 102, 241, ${s.excitation * 0.18})`;
                ctx.fill();
            }

            // Star Core Point
            ctx.beginPath();
            ctx.arc(s.x, s.y, currentRadius, 0, Math.PI * 2);
            if (s.excitation > 0.15) {
                ctx.fillStyle = `rgba(79, 70, 229, ${alpha})`;
            } else {
                ctx.fillStyle = `rgba(30, 41, 59, ${alpha})`;
            }
            ctx.fill();
        }

        requestAnimationFrame(renderConstellation);
    }
    requestAnimationFrame(renderConstellation);

    // =========================================================================
    // 2. INITIAL TELEMETRY FETCH
    // =========================================================================
    fetchStatus();

    async function fetchStatus() {
        try {
            const res = await fetch('/api/status');
            if (!res.ok) throw new Error('Failed to fetch status');
            const data = await res.json();
            renderStatusHeader(data);
        } catch (err) {
            console.error('Error in status fetch:', err);
            headerDatasetName.textContent = 'Connection Issue';
        }
    }

    function renderStatusHeader(data) {
        headerDatasetName.textContent = data.table_name || 'E-commerce_Orders';
        headerDatasetMeta.textContent = `${Number(data.row_count || 0).toLocaleString()} rows • ${data.col_count || 0} cols`;

        // Starter Pills
        if (data.starter_prompts && data.starter_prompts.length > 0) {
            heroSearchInput.placeholder = data.starter_prompts[0];
            starterPillsList.innerHTML = '';
            data.starter_prompts.slice(0, 4).forEach(p => {
                const pill = document.createElement('button');
                pill.type = 'button';
                pill.className = 'starter-pill';
                pill.textContent = p;
                pill.addEventListener('click', () => {
                    heroSearchInput.value = p;
                    executeAnalysis(p);
                });
                starterPillsList.appendChild(pill);
            });
        }

        // Telemetry Drawer Setup
        if (data.column_summaries) {
            telemetryScoreBadge.textContent = `${data.quality_score || 100}% Quality`;
            telemetryMeasuresContainer.innerHTML = '';
            (data.column_summaries.measures || []).forEach(m => {
                const div = document.createElement('div');
                div.className = 'telemetry-item';
                div.innerHTML = `<strong># ${escapeHtml(m.name)}</strong> <span>Total: ${escapeHtml(m.total)} · Avg: ${escapeHtml(m.average)}</span>`;
                telemetryMeasuresContainer.appendChild(div);
            });

            telemetryDimensionsContainer.innerHTML = '';
            (data.column_summaries.dimensions || []).forEach(d => {
                const div = document.createElement('div');
                div.className = 'telemetry-item';
                div.innerHTML = `<strong>@ ${escapeHtml(d.name)}</strong> <span>${d.cardinality} distinct</span>`;
                telemetryDimensionsContainer.appendChild(div);
            });
        }
    }

    // In-App Toast & Dynamic Query Guidance
    function showNotification(message, type = 'warning', suggestions = []) {
        let container = document.getElementById('hugr-toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'hugr-toast-container';
            container.className = 'hugr-toast-container';
            document.body.appendChild(container);
        }

        const toast = document.createElement('div');
        toast.className = `hugr-toast hugr-toast-${type}`;

        let html = `<div class="toast-header"><span class="toast-icon">✦</span><span class="toast-msg">${escapeHtml(message)}</span><button type="button" class="toast-close">&times;</button></div>`;
        if (suggestions && suggestions.length > 0) {
            html += `<div class="toast-suggestions-label">Try one of these queries:</div><div class="toast-suggestions-list">`;
            suggestions.forEach(s => {
                html += `<button type="button" class="toast-pill">${escapeHtml(s)}</button>`;
            });
            html += `</div>`;
        }
        toast.innerHTML = html;

        toast.querySelector('.toast-close').addEventListener('click', () => toast.remove());
        toast.querySelectorAll('.toast-pill').forEach(btn => {
            btn.addEventListener('click', () => {
                const queryText = btn.textContent;
                toast.remove();
                executeAnalysis(queryText);
            });
        });

        container.appendChild(toast);
        setTimeout(() => {
            if (toast.parentNode) toast.remove();
        }, 9000);
    }

    // =========================================================================
    // 3. TRANSITION & QUERY EXECUTION (SLOW STAGGERED ENTRANCE)
    // =========================================================================
    heroSearchForm.addEventListener('submit', (e) => {
        e.preventDefault();
        let q = heroSearchInput.value.trim();
        if (!q && heroSearchInput.placeholder && !heroSearchInput.placeholder.includes("Ask anything about your data")) {
            q = heroSearchInput.placeholder;
        }
        if (q) {
            executeAnalysis(q);
        } else {
            heroSearchInput.focus();
            showNotification("Please enter an analytical question or click one of the suggested queries below.", "info");
        }
    });

    headerQueryForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const q = headerQueryInput.value.trim();
        if (q) {
            headerQueryForm.classList.add('hidden');
            queryPillBubble.classList.remove('hidden');
            executeAnalysis(q);
        }
    });

    queryPillBubble.addEventListener('click', () => {
        queryPillBubble.classList.add('hidden');
        headerQueryForm.classList.remove('hidden');
        headerQueryInput.value = currentQuestion;
        headerQueryInput.focus();
        headerQueryInput.select();
    });

    btnCloseHeaderQuery.addEventListener('click', () => {
        headerQueryForm.classList.add('hidden');
        queryPillBubble.classList.remove('hidden');
    });

    btnCardFollowup.addEventListener('click', () => {
        queryPillBubble.click();
    });

    async function executeAnalysis(question) {
        currentQuestion = question;
        heroSearchInput.blur();
        headerQueryInput.blur();
        studioLoader.classList.remove('hidden');

        try {
            const res = await fetch('/api/ask', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question })
            });

            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Analysis execution failed');

            if (!data.ok) {
                showNotification(data.refusal || 'Could not answer query for this dataset.', 'warning', data.suggestions || []);
                return;
            }

            materializeStudio(data);
        } catch (err) {
            showNotification('Analysis notice: ' + err.message, 'error');
        } finally {
            studioLoader.classList.add('hidden');
        }
    }

    // Materialize 6-Card Studio
    function materializeStudio(data) {
        document.body.setAttribute('data-view', 'studio');
        initialView.classList.add('hidden');
        studioView.classList.remove('hidden');

        // Reveal Header Query Pill
        queryPillContainer.classList.remove('hidden');
        queryPillBubble.classList.remove('hidden');
        headerQueryForm.classList.add('hidden');
        activeQueryText.textContent = data.question || currentQuestion;

        // 1. Bar Chart (Card 1)
        if (data.primary_chart) {
            const fig = data.primary_chart;
            if (fig.layout && fig.layout.title && fig.layout.title.text) {
                barChartTitle.textContent = fig.layout.title.text;
                fig.layout.title = undefined; // render title cleanly in card header
            }
            renderPlotlyStudio(plotBarChart, fig);
        }

        // 2. KPI Cluster (Card 2)
        if (data.studio_kpis) {
            const k = data.studio_kpis;
            if (k.primary) {
                kpiPrimaryLabel.textContent = k.primary.label;
                kpiPrimaryValue.textContent = k.primary.value;
                kpiComparisonText.textContent = k.primary.comparison;
            }
            if (k.unique) {
                kpiSub1Label.textContent = k.unique.label;
                kpiSub1Value.textContent = k.unique.value;
                kpiSub1Note.textContent = k.unique.note;
            }
            if (k.average) {
                kpiSub2Label.textContent = k.average.label;
                kpiSub2Value.textContent = k.average.value;
                kpiSub2Note.textContent = k.average.note;
            }
        }

        // 3. Donut Chart (Card 3)
        if (data.share_chart) {
            const fig = data.share_chart;
            if (fig.layout && fig.layout.title && fig.layout.title.text) {
                donutChartTitle.textContent = fig.layout.title.text;
                fig.layout.title = undefined;
            }
            renderPlotlyStudio(plotDonutChart, fig);
        }

        // 4. Trend Chart (Card 4)
        if (data.trend_chart) {
            const fig = data.trend_chart;
            if (fig.layout && fig.layout.title && fig.layout.title.text) {
                trendChartTitle.textContent = fig.layout.title.text;
                fig.layout.title = undefined;
            }
            renderPlotlyStudio(plotTrendChart, fig);
        }

        // 5. Ranking Table (Card 5)
        if (data.top_ranking && data.top_ranking.length > 0) {
            rankingTableBody.innerHTML = data.top_ranking.map(r => {
                return `
                    <tr>
                        <td class="col-name">
                            <div class="item-name-cell">
                                <span class="item-badge-icon">${r.icon || '📦'}</span>
                                <span>${escapeHtml(r.name)}</span>
                            </div>
                        </td>
                        <td class="col-qty"><span class="item-qty-val">${escapeHtml(r.value)}</span></td>
                        <td class="col-share"><span class="item-share-pill">${escapeHtml(r.share)}</span></td>
                    </tr>
                `;
            }).join('');
        }

        // 6. Key Takeaways (Card 6)
        if (data.takeaways && data.takeaways.length > 0) {
            takeawaysList.innerHTML = data.takeaways.map(t => {
                return `<li>${t}</li>`;
            }).join('');
        }

        // Bottom Follow-up Chips
        bottomFollowupChips.innerHTML = '';
        if (data.follow_ups && data.follow_ups.length > 0) {
            data.follow_ups.slice(0, 3).forEach(f => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'followup-chip';
                chip.textContent = `↳ ${f}`;
                chip.addEventListener('click', () => executeAnalysis(f));
                bottomFollowupChips.appendChild(chip);
            });
        }

        // Update Audit Drawer Content
        if (data.records && data.records.length > 0 && data.columns) {
            auditRowsBadge.textContent = `${data.records.length} records`;
            auditTableHead.innerHTML = `<tr>${data.columns.map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr>`;
            auditTableBody.innerHTML = data.records.map(row => {
                return `<tr>${data.columns.map(c => `<td>${row[c] !== null && row[c] !== undefined ? escapeHtml(String(row[c])) : '-'}</td>`).join('')}</tr>`;
            }).join('');
        }
        if (data.plan) {
            auditPlanJson.textContent = JSON.stringify(data.plan, null, 2);
        }

        // Resize Plotly charts after entrance animation settles
        setTimeout(triggerChartResize, 350);
    }

    function renderPlotlyStudio(targetElem, figureJson) {
        if (!window.Plotly || !figureJson) return;

        const layout = Object.assign({}, figureJson.layout || {}, {
            autosize: true,
            paper_bgcolor: 'rgba(255, 255, 255, 0)',
            plot_bgcolor: 'rgba(255, 255, 255, 0)',
            font: { family: 'Inter, sans-serif', color: '#475569', size: 10.5 },
            margin: figureJson.layout && figureJson.layout.margin ? figureJson.layout.margin : { l: 35, r: 15, t: 15, b: 35 },
        });

        const config = {
            responsive: true,
            displayModeBar: false,
        };

        Plotly.newPlot(targetElem, figureJson.data || [], layout, config);
    }

    function triggerChartResize() {
        if (!studioView.classList.contains('hidden') && window.Plotly) {
            Plotly.Plots.resize(plotBarChart);
            Plotly.Plots.resize(plotDonutChart);
            Plotly.Plots.resize(plotTrendChart);
        }
    }

    // =========================================================================
    // 4. CLEAN FILE UPLOAD STRIP
    // =========================================================================
    uploadCapsuleStrip.addEventListener('click', () => heroFilePicker.click());

    ['dragenter', 'dragover'].forEach(name => {
        uploadCapsuleStrip.addEventListener(name, (e) => {
            e.preventDefault();
            e.stopPropagation();
            uploadCapsuleStrip.classList.add('dragover');
        });
    });

    ['dragleave', 'drop'].forEach(name => {
        uploadCapsuleStrip.addEventListener(name, (e) => {
            e.preventDefault();
            e.stopPropagation();
            uploadCapsuleStrip.classList.remove('dragover');
        });
    });

    uploadCapsuleStrip.addEventListener('drop', (e) => {
        const files = e.dataTransfer.files;
        if (files && files.length) uploadFiles(files);
    });

    heroFilePicker.addEventListener('change', () => {
        if (heroFilePicker.files && heroFilePicker.files.length) {
            uploadFiles(heroFilePicker.files);
        }
    });

    async function uploadFiles(fileList) {
        studioLoader.classList.remove('hidden');
        const formData = new FormData();
        for (let i = 0; i < fileList.length; i++) {
            formData.append('files', fileList[i]);
        }

        try {
            const res = await fetch('/api/upload', {
                method: 'POST',
                body: formData,
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Upload failed');

            resetToInitial();
            renderStatusHeader(data);
        } catch (err) {
            showNotification('Upload notice: ' + err.message, 'error');
        } finally {
            studioLoader.classList.add('hidden');
        }
    }

    // =========================================================================
    // 5. DRAWERS & RESET NAVIGATION
    // =========================================================================
    function resetToInitial() {
        document.body.setAttribute('data-view', 'initial');
        studioView.classList.add('hidden');
        initialView.classList.remove('hidden');
        queryPillContainer.classList.add('hidden');
        heroSearchInput.value = '';
        headerQueryInput.value = '';
    }

    brandHome.addEventListener('click', resetToInitial);
    btnNewReset.addEventListener('click', resetToInitial);

    // Export Dropdown Toggle
    btnExportTrigger.addEventListener('click', (e) => {
        e.stopPropagation();
        exportDropdownMenu.classList.toggle('hidden');
    });

    document.addEventListener('click', () => {
        if (!exportDropdownMenu.classList.contains('hidden')) {
            exportDropdownMenu.classList.add('hidden');
        }
    });

    // Drawer Toggles
    btnOpenTableDrawer.addEventListener('click', () => {
        auditDrawerOverlay.classList.remove('hidden');
    });

    btnCloseAudit.addEventListener('click', () => {
        auditDrawerOverlay.classList.add('hidden');
    });

    auditDrawerOverlay.addEventListener('click', (e) => {
        if (e.target === auditDrawerOverlay) auditDrawerOverlay.classList.add('hidden');
    });

    datasetPill.addEventListener('click', () => {
        telemetryDrawerOverlay.classList.remove('hidden');
    });

    btnCloseTelemetry.addEventListener('click', () => {
        telemetryDrawerOverlay.classList.add('hidden');
    });

    telemetryDrawerOverlay.addEventListener('click', (e) => {
        if (e.target === telemetryDrawerOverlay) telemetryDrawerOverlay.classList.add('hidden');
    });

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
});
