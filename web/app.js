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
    // 1. STAR CONSTELLATION AMBIENT CANVAS (CELESTIAL & INTERACTIVE)
    // =========================================================================
    const canvas = document.getElementById('constellation-canvas');
    const ctx = canvas.getContext('2d');
    let width = (canvas.width = window.innerWidth);
    let height = (canvas.height = window.innerHeight);

    window.addEventListener('resize', () => {
        width = canvas.width = window.innerWidth;
        height = canvas.height = window.innerHeight;
        triggerChartResize();
    });

    const NUM_STARS = 75;
    const stars = [];
    const mouse = { x: -1000, y: -1000 };

    window.addEventListener('mousemove', (e) => {
        mouse.x = e.clientX;
        mouse.y = e.clientY;
    });

    for (let i = 0; i < NUM_STARS; i++) {
        stars.push({
            x: Math.random() * width,
            y: Math.random() * height,
            radius: Math.random() * 1.5 + 0.8,
            baseAlpha: Math.random() * 0.5 + 0.25,
            phase: Math.random() * Math.PI * 2,
            twinkleSpeed: Math.random() * 0.02 + 0.01,
            vx: (Math.random() - 0.5) * 0.16,
            vy: (Math.random() - 0.5) * 0.16,
            color: i % 4 === 0 ? 'rgba(59, 130, 246,' : (i % 4 === 1 ? 'rgba(99, 102, 241,' : (i % 4 === 2 ? 'rgba(139, 92, 246,' : 'rgba(148, 163, 184,'))
        });
    }

    function renderConstellation() {
        ctx.clearRect(0, 0, width, height);

        // Update & Draw Stars
        for (let i = 0; i < stars.length; i++) {
            const s = stars[i];
            s.x += s.vx;
            s.y += s.vy;
            s.phase += s.twinkleSpeed;

            // Wrap edges
            if (s.x < 0) s.x = width;
            if (s.x > width) s.x = 0;
            if (s.y < 0) s.y = height;
            if (s.y > height) s.y = 0;

            const alpha = Math.max(0.15, Math.min(0.9, s.baseAlpha + Math.sin(s.phase) * 0.25));

            // Cursor proximity glow
            const dx = s.x - mouse.x;
            const dy = s.y - mouse.y;
            const dist = Math.hypot(dx, dy);
            let glowBoost = 0;
            if (dist < 140) {
                glowBoost = (140 - dist) / 140 * 0.45;
            }

            ctx.beginPath();
            ctx.arc(s.x, s.y, s.radius + glowBoost * 1.2, 0, Math.PI * 2);
            ctx.fillStyle = s.color + (alpha + glowBoost) + ')';
            ctx.fill();
        }

        // Connect Nearby Stars with Fine Constellation Lines
        for (let i = 0; i < stars.length; i++) {
            for (let j = i + 1; j < stars.length; j++) {
                const s1 = stars[i];
                const s2 = stars[j];
                const d = Math.hypot(s1.x - s2.x, s1.y - s2.y);
                if (d < 115) {
                    const lineAlpha = (1 - d / 115) * 0.16;
                    ctx.beginPath();
                    ctx.moveTo(s1.x, s1.y);
                    ctx.lineTo(s2.x, s2.y);
                    ctx.strokeStyle = `rgba(59, 130, 246, ${lineAlpha})`;
                    ctx.lineWidth = 0.85;
                    ctx.stroke();
                }
            }

            // Connect to mouse if near
            const dMouse = Math.hypot(stars[i].x - mouse.x, stars[i].y - mouse.y);
            if (dMouse < 130) {
                const mAlpha = (1 - dMouse / 130) * 0.22;
                ctx.beginPath();
                ctx.moveTo(stars[i].x, stars[i].y);
                ctx.lineTo(mouse.x, mouse.y);
                ctx.strokeStyle = `rgba(99, 102, 241, ${mAlpha})`;
                ctx.lineWidth = 0.9;
                ctx.stroke();
            }
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
