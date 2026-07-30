document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initWebSocket();
    initData();
    initMap();
    startUptimeTimer();
    
    // Add Enter key support for the input box
    const testInput = document.getElementById('test-message-input');
    if (testInput) {
        testInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') submitTestMessage();
        });
    }
});

async function initData() {
    await fetchSystemConfig();
    await fetchIncidents();
    await fetchResources();
    await fetchIntelRecommendation();
}

// --- Navigation ---
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    const views = document.querySelectorAll('.view');

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            // Update active nav
            navItems.forEach(n => n.classList.remove('active'));
            item.classList.add('active');

            // Show target view
            const targetViewId = 'view-' + item.dataset.view;
            views.forEach(v => {
                if (v.id === targetViewId) {
                    v.classList.add('active');
                    v.classList.remove('hidden');
                    
                    // Fix Leaflet rendering bug when map container becomes visible
                    if (targetViewId === 'view-map-view' && typeof liveMap !== 'undefined' && liveMap) {
                        setTimeout(() => {
                            liveMap.invalidateSize();
                        }, 50);
                    }
                } else {
                    v.classList.remove('active');
                    v.classList.add('hidden');
                }
            });
        });
    });
}

// --- Data Fetching ---
async function fetchIncidents() {
    try {
        const response = await fetch('/api/incidents');
        const data = await response.json();
        
        const feedContainer = document.getElementById('triage-feed');
        feedContainer.innerHTML = '';
        
        document.getElementById('active-emergencies-count').innerText = data.incidents.length;
        
        if (incidentMarkersLayer) {
            incidentMarkersLayer.clearLayers();
        }

        data.incidents.forEach(inc => {
            const urgencyClass = (inc.urgency || 'unknown').toLowerCase();
            const icon = urgencyClass === 'high' ? 'fa-triangle-exclamation' : 
                         urgencyClass === 'medium' ? 'fa-circle-exclamation' : 'fa-info-circle';
            const timeStr = new Date(inc.timestamp * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            
            const displayLocation = (inc.location && inc.location !== 'Unknown') ? inc.location : 'AUTO-LOCATE';
            const displayType = (inc.emergency_type || 'GENERAL').toUpperCase();
            
            const card = document.createElement('div');
            card.className = `incident-card ${urgencyClass}`;
            card.innerHTML = `
                <div class="card-top">
                    <span class="tag ${urgencyClass}">${inc.urgency}</span>
                    <span class="sector-tag"><i class="fa-solid fa-location-dot"></i> ${displayLocation}</span>
                    <span class="time-tag ml-auto" style="margin-left: auto;">${timeStr}</span>
                </div>
                <div class="card-message mt-2">${inc.raw_message}</div>
                <div class="card-bottom">
                    <span class="priority-label ${urgencyClass}"><i class="fa-solid fa-triangle-exclamation"></i> ${displayType}</span>
                    <span><i class="fa-solid fa-users"></i> ${inc.people_affected}</span>
                    <button class="btn-text highlight" style="padding: 2px 8px; font-size: 11px;" onclick="handleResolve(event, ${inc.id})">RESOLVE</button>
                </div>
            `;
            
            // On click, update the side panel
            card.addEventListener('click', () => {
                document.querySelectorAll('.incident-card').forEach(c => c.style.borderColor = 'var(--border-color)');
                card.style.borderColor = urgencyClass === 'high' ? 'var(--accent-red)' : 'var(--accent-blue)';
                
                document.getElementById('active-sel-id').innerText = `ID: INC-${inc.id}`;
                document.getElementById('active-ai-action').innerText = inc.action_required || 'Reviewing situation...';
                
                const confScore = Math.round(inc.confidence || 0);
                const progressFill = document.querySelector('.progress-fill');
                if (progressFill) {
                    progressFill.style.width = `${confScore}%`;
                    progressFill.innerText = `${confScore}%`;
                }
                const confBarText = document.querySelector('.confidence-bar span:first-child');
                if (confBarText && confBarText.innerText.includes('CONFIDENCE')) {
                    confBarText.innerText = `${confScore}% CONFIDENCE`;
                }
                
                updateActiveResourcesPanel(inc.id, inc.recommended_resources);
            });
            
            
            feedContainer.appendChild(card);
            
            // Plot on Map if location is known
            if (incidentMarkersLayer && inc.location && inc.location !== 'Unknown') {
                fetch(`/api/geocode?q=${encodeURIComponent(inc.location)}`)
                    .then(r => r.json())
                    .then(coords => {
                        let lat = coords.lat;
                        let lng = coords.lng;
                        
                        // Fallback to user device coords if API misses
                        if (!lat || !lng) {
                            if (navigator.geolocation && liveMap) {
                                const center = liveMap.getCenter();
                                // slight random offset
                                lat = center.lat + (Math.random() * 0.02 - 0.01);
                                lng = center.lng + (Math.random() * 0.02 - 0.01);
                            } else {
                                return; // Give up
                            }
                        }
                        
                        const markerColor = urgencyClass === 'high' ? '#ef4444' : urgencyClass === 'medium' ? '#f59e0b' : '#3b82f6';
                        L.circleMarker([lat, lng], {
                            radius: 8,
                            fillColor: markerColor,
                            color: '#fff',
                            weight: 2,
                            opacity: 1,
                            fillOpacity: 0.8
                        }).addTo(incidentMarkersLayer).bindPopup(`<b>${displayType}</b><br>${displayLocation}`);
                    });
            }
        });
        
    } catch (err) {
        console.error("Failed to fetch incidents", err);
    }
}

async function fetchResources() {
    try {
        const response = await fetch('/api/resources');
        const data = await response.json();
        
        const table = document.getElementById('deployments-table');
        table.innerHTML = '';
        
        data.resources.forEach(res => {
            const statusClass = res.status.toLowerCase().replace('_', '-');
            const row = document.createElement('tr');
            row.innerHTML = `
                <td><strong>${res.unit_code}</strong> <span class="dim">(${res.personnel_count})</span></td>
                <td><i class="fa-solid fa-truck"></i> ${res.category.replace('_', ' ')}</td>
                <td><span class="status-pill ${statusClass}">${res.status.replace('_', ' ')}</span></td>
                <td class="dim">0${res.eta_minutes}:00</td>
            `;
            table.appendChild(row);
        });
    } catch (err) {
        console.error("Failed to fetch resources", err);
    }
}

async function fetchIntelRecommendation() {
    try {
        const response = await fetch('/api/resources/recommendation');
        const data = await response.json();
        
        document.getElementById('intel-title').innerText = data.title;
        document.getElementById('intel-confidence').innerText = data.confidence + '% CONFIDENCE';
        document.getElementById('dynamic-intel-text').innerText = data.recommendation;
        
        const execBtn = document.getElementById('btn-intel-execute');
        if (data.actionable) {
            execBtn.style.display = 'inline-block';
            execBtn.dataset.incidentId = data.incident_id;
        } else {
            execBtn.style.display = 'none';
        }
    } catch (err) {
        console.error("Failed to fetch intel", err);
    }
}

async function fetchSystemConfig() {
    try {
        const response = await fetch('/api/system/config');
        const config = await response.json();
        
        document.getElementById('config-provider-name').innerText = "Cloud Inference";
        document.getElementById('config-provider-desc').innerText = `Gemma ECC is currently running. Model: ${config.model}. Network connected. Accelerated inference active.`;
        document.getElementById('sidebar-cloud-status').innerText = `SYSTEM ACTIVE`;
    } catch (err) {
        console.error("Failed to fetch system config", err);
    }
}

// --- Map Integration ---
let liveMap = null;
let incidentMarkersLayer = null;

async function initMap() {
    // Initialize map centered roughly over Chicago as fallback
    liveMap = L.map('live-map', {
        zoomControl: false,
        attributionControl: false
    }).setView([41.8781, -87.6298], 11);
    
    incidentMarkersLayer = L.layerGroup().addTo(liveMap);

    // Try to get user's current location to center map
    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition((position) => {
            const lat = position.coords.latitude;
            const lng = position.coords.longitude;
            liveMap.setView([lat, lng], 11);
            
            // Add a radar blip for the user's base station
            L.circleMarker([lat, lng], {
                radius: 6,
                fillColor: '#3b82f6',
                color: '#fff',
                weight: 2,
                opacity: 1,
                fillOpacity: 1
            }).addTo(liveMap).bindPopup('<b>COMMAND CENTER (YOU)</b>').openPopup();
        }, () => {
            console.log("Geolocation denied or unavailable. Using default base.");
        });
    }

    // Add dark theme tile layer
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        subdomains: 'abcd',
        maxZoom: 19
    }).addTo(liveMap);
    
    // Fetch and plot clusters
    try {
        const response = await fetch('/api/clusters');
        const data = await response.json();
        
        data.clusters.forEach(cluster => {
            const color = cluster.risk_level === 'CRITICAL' ? '#ef4444' : '#f59e0b';
            
            // Draw a circle for the impact radius
            L.circle([cluster.lat, cluster.lng], {
                color: color,
                fillColor: color,
                fillOpacity: 0.15,
                radius: cluster.impact_radius_miles * 1609.34 // convert miles to meters
            }).addTo(liveMap);
            
            // Draw marker
            const marker = L.circleMarker([cluster.lat, cluster.lng], {
                radius: 8,
                fillColor: color,
                color: '#fff',
                weight: 2,
                opacity: 1,
                fillOpacity: 1
            }).addTo(liveMap);
            
            // Add click event to update Map View side panel
            marker.on('click', () => {
                document.getElementById('cluster-name').innerText = cluster.name;
                document.getElementById('cluster-risk').innerText = cluster.risk_level;
                document.getElementById('cluster-risk').style.color = color;
                document.getElementById('cluster-risk').style.backgroundColor = color + '22';
                document.getElementById('cluster-risk').style.borderColor = color + '55';
                
                document.getElementById('cluster-assessment').innerText = cluster.ai_damage_assessment;
                document.getElementById('cluster-radius').innerText = cluster.impact_radius_miles + 'mi';
                document.getElementById('cluster-civilians').innerText = cluster.estimated_civilians.toLocaleString();
            });
        });
    } catch (err) {
        console.error("Failed to load clusters", err);
    }
}

async function submitTestMessage() {
    const input = document.getElementById('test-message-input');
    const btn = document.getElementById('test-submit-btn');
    const msg = input.value.trim();
    
    if (!msg) return;
    
    // UI Loading state
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    btn.disabled = true;
    input.disabled = true;
    
    try {
        const response = await fetch('/api/test-analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg })
        });
        
        if (response.ok) {
            input.value = '';
            
            const result = await response.json();
            if (result.record && result.record.latency_sec) {
                const terminal = document.getElementById('terminal-log');
                const now = new Date();
                const timeStr = now.toTimeString().split(' ')[0];
                terminal.innerHTML += `\n[${timeStr}] Real inference processed in ${(result.record.latency_sec * 1000).toFixed(0)}ms`;
                terminal.scrollTop = terminal.scrollHeight;
            }
            
            // Refresh feed to show new message at top
            await fetchIncidents();
        }
    } catch (err) {
        console.error("Failed to submit message", err);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-paper-plane"></i>';
        btn.disabled = false;
        input.disabled = false;
        input.focus();
    }
}

// --- Telemetry WebSocket ---
function initWebSocket() {
    const ws = new WebSocket(`ws://${window.location.host}/ws/telemetry`);
    
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        
        // Update bars
        document.getElementById('tel-cpu').innerText = `${data.cpu_percent}%`;
        document.getElementById('bar-cpu').style.width = `${data.cpu_percent}%`;
        
        document.getElementById('tel-ram').innerText = `${data.ram_used_gb} GB / ${data.ram_total_gb} GB`;
        document.getElementById('bar-ram').style.width = `${(data.ram_used_gb / data.ram_total_gb) * 100}%`;
    };
}

async function updateActiveResourcesPanel(incidentId, defaultRec) {
    const resourceList = document.getElementById('active-resources');
    try {
        const response = await fetch('/api/resources');
        const data = await response.json();
        const assigned = data.resources.filter(r => r.assigned_incident_id === incidentId);
        
        if (assigned.length > 0) {
            resourceList.innerHTML = assigned.map(r => `<li><span><i class="fa-solid fa-truck-fast"></i> ${r.unit_code} (${r.category.replace('_', ' ')})</span> <span class="text-danger">EN ROUTE</span></li>`).join('');
        } else {
            resourceList.innerHTML = `<li><span class="dim">RECOMMENDED:</span></li><li><span><i class="fa-solid fa-truck-fast"></i> ${defaultRec || 'Standard Unit'}</span> <span>ETA 4m</span></li>`;
        }
    } catch (e) {
        console.error(e);
    }
}

// --- Interactive UI Actions ---
async function handleDeploy(btn) {
    if (btn.innerText.includes('DEPLOYED')) return;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> TRANSMITTING...';
    btn.style.opacity = '0.7';
    const idText = document.getElementById('active-sel-id').innerText;
    const incidentId = parseInt(idText.replace('ID: INC-', ''));
    
    try {
        const response = await fetch('/api/deploy_next_available', { 
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({incident_id: incidentId || null})
        });
        const result = await response.json();
        
        if (response.ok && result.success) {
            btn.innerHTML = '<i class="fa-solid fa-check"></i> DEPLOYED';
            btn.style.backgroundColor = 'var(--accent-green)';
            btn.style.opacity = '1';
            
            // Refresh resource table to show the newly deployed unit dynamically!
            await fetchResources();
            await updateActiveResourcesPanel(incidentId, null);
            
            // Add a terminal log
            const terminal = document.getElementById('terminal-log');
            const now = new Date();
            const timeStr = now.toTimeString().split(' ')[0];
            terminal.innerHTML += `\n[${timeStr}] SYSTEM: Dispatch order confirmed. Asset ${result.deployed_unit} en route.`;
            terminal.scrollTop = terminal.scrollHeight;
        } else {
            btn.innerHTML = 'ERROR (No Units)';
            btn.style.opacity = '1';
        }
    } catch (err) {
        console.error("Deploy error", err);
    }
}

function handleStandby(btn) {
    if (btn.innerText.includes('ACKNOWLEDGED')) return;
    btn.innerHTML = '<i class="fa-solid fa-check-double"></i> ACKNOWLEDGED';
    btn.style.backgroundColor = '#1e293b';
}

async function handleEvac(btn) {
    if (btn.innerText.includes('ISSUED')) return;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> BROADCASTING...';
    
    try {
        await fetch('/api/evac_order', { method: 'POST' });
        
        btn.innerHTML = '<i class="fa-solid fa-tower-broadcast"></i> ORDER ISSUED';
        btn.style.backgroundColor = 'var(--accent-green)';
    } catch (err) {
        console.error("Evac error", err);
    }
}

async function handleIntelExecute(btn) {
    if (btn.innerText.includes('EXECUTED')) return;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    
    const targetIncident = btn.dataset.incidentId;
    
    try {
        const response = await fetch('/api/deploy_next_available', { 
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({incident_id: targetIncident ? parseInt(targetIncident) : null})
        });
        const result = await response.json();
        
        if (response.ok && result.success) {
            btn.innerHTML = '<i class="fa-solid fa-check"></i> EXECUTED';
            btn.style.color = 'var(--accent-green)';
            btn.parentElement.parentElement.style.borderColor = 'var(--accent-green)';
            
            // Refresh the resources table from the DB!
            await fetchResources();
            await fetchIntelRecommendation();
            
            // Highlight the modified row
            setTimeout(() => {
                const table = document.getElementById('deployments-table');
                if (table) {
                    const rows = table.getElementsByTagName('tr');
                    for (let row of rows) {
                        if (row.innerText.includes(result.deployed_unit)) {
                            row.style.backgroundColor = 'rgba(16, 185, 129, 0.2)';
                        }
                    }
                }
            }, 100);
        } else {
            btn.innerHTML = 'NO UNITS';
        }
    } catch (err) {
        console.error("Failed to execute command", err);
        btn.innerHTML = 'ERROR';
    }
}

async function handleNewDeployment(btn) {
    if (btn.innerText.includes('DEPLOYED')) return;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> DISPATCHING...';
    
    try {
        const response = await fetch('/api/deploy_next_available', { method: 'POST' });
        const result = await response.json();
        
        if (response.ok && result.success) {
            btn.innerHTML = '<i class="fa-solid fa-check"></i> DISPATCHED ' + result.deployed_unit;
            btn.style.backgroundColor = 'var(--accent-green)';
            await fetchResources(); // reload the table
            
            setTimeout(() => {
                btn.innerHTML = 'NEW DEPLOYMENT';
                btn.style.backgroundColor = '';
            }, 3000);
        } else {
            btn.innerHTML = 'NO UNITS AVAIL';
            setTimeout(() => { btn.innerHTML = 'NEW DEPLOYMENT'; }, 2000);
        }
    } catch (err) {
        console.error("New deployment failed", err);
    }
}

async function handleResolve(event, incidentId) {
    event.stopPropagation(); // Prevent clicking the card
    const btn = event.currentTarget;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    
    try {
        await fetch(`/api/incident/${incidentId}/resolve`, { method: 'POST' });
        // Fade out the card
        const card = btn.closest('.incident-card');
        card.style.opacity = '0';
        setTimeout(() => {
            fetchIncidents(); // re-fetch the feed
            
            // Clear active selection if it was the resolved one
            if (document.getElementById('active-sel-id').innerText.includes(`INC-${incidentId}`)) {
                document.getElementById('active-sel-id').innerText = 'Select an incident';
                document.getElementById('active-ai-action').innerText = 'Reviewing situation...';
                document.getElementById('active-resources').innerHTML = '<li>Select an incident.</li>';
            }
        }, 300);
    } catch (err) {
        console.error("Failed to resolve", err);
        btn.innerHTML = 'ERROR';
    }
}

// --- Utils ---
function startUptimeTimer() {
    let seconds = 0;
    setInterval(() => {
        seconds++;
        const h = Math.floor(seconds / 3600).toString().padStart(2, '0');
        const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, '0');
        const s = (seconds % 60).toString().padStart(2, '0');
        document.getElementById('uptime-counter').innerText = `${h}:${m}:${s}`;
    }, 1000);
}
