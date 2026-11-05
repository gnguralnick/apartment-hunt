(function () {
    "use strict";

    // ── state ──────────────────────────────────────────────
    var allListings = [];      // raw from server (with annotations)
    var displayedListings = []; // after hide-filter + sort
    var showHidden = false;
    var currentSort = "score";
    var config = null;         // mirrors server config
    var markers = [];
    var map = null;
    var activeMarkerEl = null;
    var waypointsData = [];

    // ── DOM refs ───────────────────────────────────────────
    var subtitle = document.getElementById("subtitle");
    var cardsList = document.getElementById("cards-list");
    var sortSelect = document.getElementById("sort-select");
    var hiddenToggle = document.getElementById("hidden-toggle");
    var scrapeBtn = document.getElementById("scrape-btn");
    var configBtn = document.getElementById("config-btn");
    var newBanner = document.getElementById("new-banner");
    var newBannerText = document.getElementById("new-banner-text");
    var dismissNew = document.getElementById("dismiss-new");
    var scrapeProgress = document.getElementById("scrape-progress");
    var sidePanel = document.getElementById("side-panel");

    // ── init ───────────────────────────────────────────────
    fetchListings();
    fetchConfig();

    sortSelect.addEventListener("change", function () {
        currentSort = this.value;
        renderCards();
    });

    hiddenToggle.addEventListener("click", function () {
        showHidden = !showHidden;
        renderCards();
    });

    scrapeBtn.addEventListener("click", startScrape);
    configBtn.addEventListener("click", toggleConfigPanel);
    dismissNew.addEventListener("click", function () {
        fetch("/api/dismiss-new", { method: "POST" });
        newBanner.classList.remove("visible");
    });

    // ── data fetching ──────────────────────────────────────
    function fetchListings() {
        fetch("/api/listings")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                allListings = data.listings;
                if (data.new_count > 0) {
                    newBannerText.textContent = data.new_count + " new listing" + (data.new_count === 1 ? "" : "s") + " since last scrape";
                    newBanner.classList.add("visible");
                } else {
                    newBanner.classList.remove("visible");
                }
                renderCards();
                renderMap();
            });
    }

    function fetchConfig() {
        fetch("/api/config")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                config = data;
                waypointsData = data.waypoints || [];
            });
    }

    // ── sorting ────────────────────────────────────────────
    function sortListings(listings) {
        var sorted = listings.slice();
        switch (currentSort) {
            case "score":
                sorted.sort(function (a, b) { return b.score - a.score; });
                break;
            case "rent-asc":
                sorted.sort(function (a, b) {
                    return (a.listing.rent || 99999) - (b.listing.rent || 99999);
                });
                break;
            case "rent-desc":
                sorted.sort(function (a, b) {
                    return (b.listing.rent || 0) - (a.listing.rent || 0);
                });
                break;
            case "sqft-desc":
                sorted.sort(function (a, b) {
                    return (b.listing.sqft || 0) - (a.listing.sqft || 0);
                });
                break;
            case "bedrooms-desc":
                sorted.sort(function (a, b) {
                    return (b.listing.bedrooms || 0) - (a.listing.bedrooms || 0);
                });
                break;
        }
        return sorted;
    }

    // ── rendering ──────────────────────────────────────────
    function renderCards() {
        var visible = allListings.filter(function (item) {
            return showHidden || !item.annotation.hidden;
        });
        displayedListings = sortListings(visible);

        var hiddenCount = allListings.filter(function (item) { return item.annotation.hidden; }).length;
        hiddenToggle.textContent = showHidden
            ? "Hide hidden (" + hiddenCount + ")"
            : "Show hidden (" + hiddenCount + ")";

        var totalVisible = allListings.filter(function (item) { return !item.annotation.hidden; }).length;
        subtitle.textContent = totalVisible + " listings scored and ranked";

        if (displayedListings.length === 0) {
            cardsList.innerHTML = '<div class="empty">No listings match your filters.</div>';
            return;
        }

        var html = "";
        for (var i = 0; i < displayedListings.length; i++) {
            html += renderCard(displayedListings[i], i);
        }
        cardsList.innerHTML = html;
        attachCardListeners();
    }

    function esc(s) {
        if (s == null) return "";
        var div = document.createElement("div");
        div.textContent = String(s);
        return div.innerHTML;
    }

    function renderCard(item, index) {
        var listing = item.listing;
        var ann = item.annotation;
        var hiddenClass = ann.hidden ? " is-hidden" : "";

        var addr = esc(listing.address);
        if (listing.unit) addr += " #" + esc(listing.unit);

        var meta = "";
        if (listing.rent != null) meta += "<span>$" + Number(listing.rent).toLocaleString() + "/mo</span>";
        if (listing.bedrooms != null) meta += "<span>" + listing.bedrooms + "bd</span>";
        if (listing.bathrooms != null) {
            var ba = listing.bathrooms === Math.floor(listing.bathrooms) ? Math.floor(listing.bathrooms) : listing.bathrooms;
            meta += "<span>" + ba + "ba</span>";
        }
        if (listing.sqft != null) meta += "<span>" + Number(listing.sqft).toLocaleString() + " sqft</span>";
        if (listing.neighborhood) meta += "<span>" + esc(listing.neighborhood) + "</span>";

        var travelHtml = "";
        if (item.waypoint_travel) {
            var wpParts = [];
            var wpNames = Object.keys(item.waypoint_travel).sort();
            for (var w = 0; w < wpNames.length; w++) {
                var wts = item.waypoint_travel[wpNames[w]];
                if (!wts || !wts.length) continue;
                var modeStrs = wts.map(function (wt) {
                    return Math.round(wt.minutes) + "min " + wt.mode;
                });
                wpParts.push("<span>" + esc(wpNames[w]) + ": " + esc(modeStrs.join(", ")) + "</span>");
            }
            if (wpParts.length) travelHtml = '<div class="card-meta">' + wpParts.join("") + "</div>";
        }

        var sources = listing.sources && listing.sources.length ? listing.sources : [listing.source];
        var linkHtml = listing.listing_url
            ? '<div class="card-link"><a href="' + esc(listing.listing_url) + '" target="_blank">View</a></div>'
            : "";
        var newBadge = item.is_new ? '<span class="new-badge">NEW</span>' : "";

        // Details
        var detailFields = [
            ["Available", listing.available_date],
            ["Lease term", listing.lease_term_months ? listing.lease_term_months + " months" : null],
            ["Pets allowed", fmtBool(listing.pets_allowed)],
            ["Cats", fmtBool(listing.cats_allowed)],
            ["Dogs", fmtBool(listing.dogs_allowed)],
            ["Pet deposit", listing.pet_deposit ? "$" + Number(listing.pet_deposit).toLocaleString() : null],
            ["Pet rent", listing.pet_rent ? "$" + Number(listing.pet_rent).toLocaleString() + "/mo" : null],
            ["Parking", listing.parking],
            ["Parking cost", listing.parking_cost ? "$" + Number(listing.parking_cost).toLocaleString() + "/mo" : null],
            ["Laundry", listing.laundry],
            ["Dishwasher", fmtBool(listing.dishwasher)],
            ["Furnished", fmtBool(listing.furnished)],
            ["Rent controlled", fmtBool(listing.rent_controlled)],
            ["Security deposit", listing.security_deposit ? "$" + Number(listing.security_deposit).toLocaleString() : null],
            ["Floor", listing.floor],
            ["Floor plan", listing.floor_plan_name],
            ["Utilities included", listing.utilities_included && listing.utilities_included.length ? listing.utilities_included.join(", ") : null],
            ["Contact", listing.contact_name],
            ["Phone", listing.contact_phone],
            ["Email", listing.contact_email],
            ["Promotions", listing.promotions]
        ];

        var gridItems = "";
        for (var d = 0; d < detailFields.length; d++) {
            var val = detailFields[d][1];
            if (val != null && val !== "" && val !== "null") {
                gridItems += '<div class="detail-item"><span class="detail-label">' + esc(detailFields[d][0]) + ':</span> <span class="detail-value">' + esc(val) + '</span></div>';
            }
        }
        if (listing.amenities && listing.amenities.length) {
            gridItems += '<div class="detail-item"><span class="detail-label">Amenities:</span> <span class="detail-value">' + esc(listing.amenities.join(", ")) + '</span></div>';
        }

        var photosHtml = "";
        if (listing.photos && listing.photos.length) {
            photosHtml = '<div class="photos">';
            for (var p = 0; p < listing.photos.length; p++) {
                photosHtml += '<img src="' + esc(listing.photos[p]) + '" alt="Listing photo" loading="lazy">';
            }
            photosHtml += "</div>";
        }

        var tourLink = listing.virtual_tour_url
            ? '<p style="margin-top:10px"><a href="' + esc(listing.virtual_tour_url) + '" target="_blank">Virtual Tour</a></p>'
            : "";

        var descHtml = listing.description
            ? '<div class="description">' + esc(listing.description) + '</div>'
            : "";

        var breakdownParts = "";
        if (item.score_breakdown) {
            var dims = Object.keys(item.score_breakdown).sort();
            for (var b = 0; b < dims.length; b++) {
                breakdownParts += "<span>" + esc(dims[b]) + ": " + item.score_breakdown[dims[b]].toFixed(1) + "</span>";
            }
        }

        // Stars
        var starsHtml = '<span class="star-rating" data-addr="' + esc(item.normalized_address) + '">';
        for (var s = 1; s <= 5; s++) {
            var filled = ann.rating != null && s <= ann.rating ? " filled" : "";
            starsHtml += '<span class="star' + filled + '" data-val="' + s + '">&#9733;</span>';
        }
        starsHtml += '</span>';

        return '<div class="card' + hiddenClass + '" id="card-' + index + '" data-addr="' + esc(item.normalized_address) + '">' +
            '<div class="card-header">' +
                '<div class="rank-badge">#' + (index + 1) + '</div>' +
                '<div class="score-badge">' + item.score.toFixed(1) + '</div>' +
                newBadge +
                '<div class="card-summary">' +
                    '<div class="card-address">' + addr + '</div>' +
                    '<div class="card-meta">' + meta + '</div>' +
                    travelHtml +
                    '<div class="card-source">via ' + esc(sources.join(", ")) + '</div>' +
                '</div>' +
                linkHtml +
                '<div class="toggle-arrow">&#9654;</div>' +
            '</div>' +
            '<div class="card-details">' +
                photosHtml +
                (gridItems ? '<div class="detail-grid">' + gridItems + '</div>' : '') +
                tourLink +
                descHtml +
                '<div class="score-breakdown">' + breakdownParts + '</div>' +
            '</div>' +
            '<div class="annotation-bar">' +
                '<button class="ann-btn hide-btn' + (ann.hidden ? " active" : "") + '" data-action="hidden">' + (ann.hidden ? "Unhide" : "Hide") + '</button>' +
                '<button class="ann-btn' + (ann.contacted ? " active" : "") + '" data-action="contacted">Contacted</button>' +
                '<button class="ann-btn' + (ann.toured ? " active" : "") + '" data-action="toured">Toured</button>' +
                starsHtml +
                '<input class="notes-input" placeholder="Notes..." value="' + esc(ann.notes || "") + '" data-action="notes">' +
            '</div>' +
        '</div>';
    }

    function fmtBool(v) {
        if (v === true) return "Yes";
        if (v === false) return "No";
        return null;
    }

    function attachCardListeners() {
        // Card toggle + map fly-to
        var headers = cardsList.querySelectorAll(".card-header");
        for (var i = 0; i < headers.length; i++) {
            headers[i].addEventListener("click", onCardHeaderClick);
        }

        // Annotation buttons
        var annBtns = cardsList.querySelectorAll(".ann-btn");
        for (var j = 0; j < annBtns.length; j++) {
            annBtns[j].addEventListener("click", onAnnotationClick);
        }

        // Star rating
        var stars = cardsList.querySelectorAll(".star-rating .star");
        for (var k = 0; k < stars.length; k++) {
            stars[k].addEventListener("click", onStarClick);
        }

        // Notes
        var notes = cardsList.querySelectorAll(".notes-input");
        for (var n = 0; n < notes.length; n++) {
            notes[n].addEventListener("click", function (e) { e.stopPropagation(); });
            notes[n].addEventListener("change", onNotesChange);
        }
    }

    function onCardHeaderClick(e) {
        if (e.target.closest("a") || e.target.closest(".ann-btn") || e.target.closest(".star") || e.target.closest(".notes-input")) return;
        var card = e.currentTarget.closest(".card");
        card.classList.toggle("open");
        if (!map) return;
        var index = parseInt(card.id.replace("card-", ""));
        var item = displayedListings[index];
        if (item && item.listing.lat && item.listing.lng) {
            map.flyTo([item.listing.lat, item.listing.lng], Math.max(map.getZoom(), 15));
            highlightMarker(index);
        }
    }

    // ── annotations ────────────────────────────────────────
    function onAnnotationClick(e) {
        e.stopPropagation();
        var btn = e.currentTarget;
        var card = btn.closest(".card");
        var addr = card.dataset.addr;
        var action = btn.dataset.action;
        var item = findItem(addr);
        if (!item) return;

        var newVal = !item.annotation[action];
        item.annotation[action] = newVal;

        fetch("/api/listings/state", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ normalized_address: addr, field: action, value: newVal })
        });

        if (action === "hidden") {
            renderCards();
        } else {
            btn.classList.toggle("active", newVal);
        }
    }

    function onStarClick(e) {
        e.stopPropagation();
        var star = e.currentTarget;
        var val = parseInt(star.dataset.val);
        var ratingEl = star.closest(".star-rating");
        var addr = ratingEl.dataset.addr;
        var item = findItem(addr);
        if (!item) return;

        // Toggle off if clicking the same rating
        var newVal = (item.annotation.rating === val) ? null : val;
        item.annotation.rating = newVal;

        // Update stars visually
        var allStars = ratingEl.querySelectorAll(".star");
        for (var i = 0; i < allStars.length; i++) {
            allStars[i].classList.toggle("filled", newVal != null && (i + 1) <= newVal);
        }

        fetch("/api/listings/state", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ normalized_address: addr, field: "rating", value: newVal })
        });
    }

    function onNotesChange(e) {
        var input = e.currentTarget;
        var card = input.closest(".card");
        var addr = card.dataset.addr;
        var item = findItem(addr);
        if (!item) return;

        item.annotation.notes = input.value;
        fetch("/api/listings/state", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ normalized_address: addr, field: "notes", value: input.value })
        });
    }

    function findItem(normalizedAddr) {
        for (var i = 0; i < allListings.length; i++) {
            if (allListings[i].normalized_address === normalizedAddr) return allListings[i];
        }
        return null;
    }

    // ── map ────────────────────────────────────────────────
    function renderMap() {
        if (typeof L === "undefined") return;

        if (!map) {
            map = L.map("map", { zoomControl: true });
            L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
                subdomains: "abcd",
                maxZoom: 19
            }).addTo(map);
        }

        // Clear old markers
        for (var m = 0; m < markers.length; m++) {
            if (markers[m]) map.removeLayer(markers[m]);
        }
        markers = [];

        var bounds = [];
        var mappableCount = 0;
        displayedListings.forEach(function (item) {
            if (item.listing.lat && item.listing.lng && !item.annotation.hidden) mappableCount++;
        });

        displayedListings.forEach(function (item, index) {
            var listing = item.listing;
            if (!listing.lat || !listing.lng) { markers[index] = null; return; }
            if (item.annotation.hidden && !showHidden) { markers[index] = null; return; }

            var rank = index + 1;
            var color = rankColor(rank, mappableCount);
            var icon = L.divIcon({
                className: "rank-icon",
                html: '<div style="background:' + color + '">' + rank + "</div>",
                iconSize: [28, 28],
                iconAnchor: [14, 14]
            });

            var addr = listing.address;
            if (listing.unit) addr += " #" + listing.unit;
            var tip = "<b>#" + rank + " " + addr + "</b>";
            if (listing.rent) tip += "<br>$" + Number(listing.rent).toLocaleString() + "/mo";
            tip += "<br>Score: " + item.score.toFixed(1);

            var marker = L.marker([listing.lat, listing.lng], {
                icon: icon,
                zIndexOffset: 1000 - rank
            }).addTo(map);
            marker.bindTooltip(tip);
            markers[index] = marker;

            marker.on("click", (function (idx) {
                return function () {
                    var card = document.getElementById("card-" + idx);
                    if (!card) return;
                    if (!card.classList.contains("open")) card.classList.add("open");
                    card.scrollIntoView({ behavior: "smooth", block: "start" });
                    card.classList.remove("highlighted");
                    void card.offsetWidth;
                    card.classList.add("highlighted");
                    highlightMarker(idx);
                };
            })(index));

            bounds.push([listing.lat, listing.lng]);
        });

        // Waypoint markers
        if (waypointsData) {
            waypointsData.forEach(function (wp) {
                var icon = L.divIcon({
                    className: "wp-icon",
                    html: "<div></div>",
                    iconSize: [16, 16],
                    iconAnchor: [8, 8]
                });
                var wpMarker = L.marker([wp.lat, wp.lng], {
                    icon: icon,
                    interactive: false,
                    zIndexOffset: 2000
                }).addTo(map);
                wpMarker.bindTooltip(wp.name, {
                    permanent: true,
                    direction: "right",
                    className: "wp-label",
                    offset: [8, 0]
                });
                bounds.push([wp.lat, wp.lng]);
            });
        }

        if (bounds.length) map.fitBounds(bounds, { padding: [30, 30] });
    }

    function rankColor(rank, total) {
        var t = total <= 1 ? 0 : (rank - 1) / (total - 1);
        var hue = Math.round(120 * (1 - t));
        return "hsl(" + hue + ", 70%, 42%)";
    }

    function highlightMarker(index) {
        if (activeMarkerEl) activeMarkerEl.classList.remove("active");
        var m = markers[index];
        if (m) {
            var el = m.getElement();
            if (el) {
                el.classList.add("active");
                activeMarkerEl = el;
            }
        }
    }

    // ── scraping ───────────────────────────────────────────
    function startScrape() {
        scrapeBtn.disabled = true;
        scrapeBtn.textContent = "Scraping...";
        scrapeProgress.classList.add("visible");
        scrapeProgress.innerHTML = '<span style="color:#1e40af">Starting scrapers...</span>';

        fetch("/api/scrape", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
        }).then(function () {
            listenScrapeProgress();
        }).catch(function (err) {
            scrapeBtn.disabled = false;
            scrapeBtn.textContent = "Scrape All";
            scrapeProgress.innerHTML = '<span style="color:#dc2626">Failed to start: ' + err + '</span>';
        });
    }

    function listenScrapeProgress() {
        var source = new EventSource("/api/scrape/progress");
        var scraperStatuses = {};

        source.onmessage = function (event) {
            var data = JSON.parse(event.data);

            if (data.type === "start") {
                data.landlords.forEach(function (slug) {
                    scraperStatuses[slug] = { status: "running" };
                });
                updateScrapeDisplay(scraperStatuses);
            } else if (data.type === "scraper_done") {
                scraperStatuses[data.slug] = {
                    status: data.success ? "done" : "failed",
                    count: data.count,
                    error: data.error,
                    elapsed: data.elapsed
                };
                updateScrapeDisplay(scraperStatuses);
            } else if (data.type === "done") {
                source.close();
                scrapeBtn.disabled = false;
                scrapeBtn.textContent = "Scrape All";
                setTimeout(function () {
                    scrapeProgress.classList.remove("visible");
                }, 3000);
                fetchListings();
            }
        };

        source.onerror = function () {
            source.close();
            scrapeBtn.disabled = false;
            scrapeBtn.textContent = "Scrape All";
        };
    }

    function updateScrapeDisplay(statuses) {
        var html = "";
        var slugs = Object.keys(statuses);
        for (var i = 0; i < slugs.length; i++) {
            var slug = slugs[i];
            var s = statuses[slug];
            if (s.status === "running") {
                html += '<span class="scraper-row">&#9696; ' + esc(slug) + '</span>';
            } else if (s.status === "done") {
                html += '<span class="scraper-row done">&#10003; ' + esc(slug) + ' (' + s.count + ', ' + s.elapsed + 's)</span>';
            } else {
                html += '<span class="scraper-row failed">&#10007; ' + esc(slug) + ' (' + esc(s.error) + ')</span>';
            }
        }
        scrapeProgress.innerHTML = html;
    }

    // ── config panel ───────────────────────────────────────
    function toggleConfigPanel() {
        if (sidePanel.classList.contains("visible")) {
            sidePanel.classList.remove("visible");
            return;
        }
        renderConfigPanel();
        sidePanel.classList.add("visible");
    }

    function renderConfigPanel() {
        if (!config) return;
        var html = '<h2>Configuration</h2>';

        // Filters section
        html += '<h3>Filters</h3>';
        html += rangeField("Rent", "filter-rent", config.filters.rent);
        html += rangeField("Bedrooms", "filter-bedrooms", config.filters.bedrooms);
        html += rangeField("Bathrooms", "filter-bathrooms", config.filters.bathrooms);
        html += rangeField("Sqft", "filter-sqft", config.filters.sqft);
        html += '<div class="config-field"><label>City</label><input id="filter-city" value="' + esc(config.filters.city || "") + '"></div>';
        html += '<div class="config-field"><label>Pets allowed</label><select id="filter-pets">';
        html += '<option value=""' + (config.filters.pets_allowed == null ? " selected" : "") + '>Any</option>';
        html += '<option value="true"' + (config.filters.pets_allowed === true ? " selected" : "") + '>Yes</option>';
        html += '<option value="false"' + (config.filters.pets_allowed === false ? " selected" : "") + '>No</option>';
        html += '</select></div>';

        // Preferences section
        html += '<h3>Preferences</h3>';
        html += '<div id="prefs-list">';
        var prefs = config.preferences || [];
        for (var i = 0; i < prefs.length; i++) {
            html += prefRow(prefs[i].name, prefs[i].tier, i);
        }
        html += '</div>';
        html += '<div style="margin-top:6px"><select id="add-pref-name" style="width:120px;font-size:0.8rem;padding:3px 6px"><option value="">Add preference...</option>';
        var prefOptions = ["dishwasher", "sqft", "rent", "in_unit_laundry", "rent_controlled", "furnished", "parking"];
        for (var p = 0; p < prefOptions.length; p++) {
            html += '<option value="' + prefOptions[p] + '">' + prefOptions[p] + '</option>';
        }
        html += '</select></div>';

        // Waypoints section
        html += '<h3>Waypoints</h3>';
        html += '<div id="waypoints-list">';
        var wps = config.waypoints || [];
        for (var w = 0; w < wps.length; w++) {
            html += waypointCard(wps[w], w);
        }
        html += '</div>';
        html += '<button class="btn btn-sm" id="add-wp-btn" style="margin-top:6px">+ Add waypoint</button>';

        // Actions
        html += '<div class="panel-actions">';
        html += '<button class="btn btn-primary" id="apply-config-btn">Apply changes</button>';
        html += '<button class="btn" id="close-config-btn">Close</button>';
        html += '</div>';

        sidePanel.innerHTML = html;

        // Attach listeners
        document.getElementById("apply-config-btn").addEventListener("click", applyConfig);
        document.getElementById("close-config-btn").addEventListener("click", function () {
            sidePanel.classList.remove("visible");
        });

        document.getElementById("add-pref-name").addEventListener("change", function () {
            if (!this.value) return;
            config.preferences.push({ name: this.value, tier: "moderate" });
            renderConfigPanel();
        });

        document.getElementById("add-wp-btn").addEventListener("click", function () {
            config.waypoints.push({
                name: "New Waypoint",
                lat: 37.78,
                lng: -122.42,
                tier: "moderate",
                modes: { walk: { max_minutes: 30 } }
            });
            renderConfigPanel();
        });

        // Remove pref buttons
        var removePrefBtns = sidePanel.querySelectorAll(".remove-pref");
        for (var rp = 0; rp < removePrefBtns.length; rp++) {
            removePrefBtns[rp].addEventListener("click", (function (idx) {
                return function () {
                    config.preferences.splice(idx, 1);
                    renderConfigPanel();
                };
            })(rp));
        }

        // Remove waypoint buttons
        var removeWpBtns = sidePanel.querySelectorAll(".remove-wp");
        for (var rw = 0; rw < removeWpBtns.length; rw++) {
            removeWpBtns[rw].addEventListener("click", (function (idx) {
                return function () {
                    config.waypoints.splice(idx, 1);
                    renderConfigPanel();
                };
            })(rw));
        }
    }

    function rangeField(label, id, range) {
        return '<div class="config-field"><label>' + label + '</label>' +
            '<div class="range-inputs">' +
            '<input id="' + id + '-min" type="number" placeholder="Min" value="' + (range.min != null ? range.min : "") + '">' +
            '<input id="' + id + '-max" type="number" placeholder="Max" value="' + (range.max != null ? range.max : "") + '">' +
            '</div></div>';
    }

    function prefRow(name, tier, index) {
        return '<div class="pref-row">' +
            '<label>' + esc(name) + '</label>' +
            '<select class="pref-tier" data-idx="' + index + '">' +
            '<option value="strong"' + (tier === "strong" ? " selected" : "") + '>Strong (10)</option>' +
            '<option value="moderate"' + (tier === "moderate" ? " selected" : "") + '>Moderate (5)</option>' +
            '<option value="low"' + (tier === "low" ? " selected" : "") + '>Low (2)</option>' +
            '</select>' +
            '<span class="remove-pref" title="Remove">&times;</span>' +
            '</div>';
    }

    function waypointCard(wp, index) {
        var walkMins = wp.modes && wp.modes.walk ? wp.modes.walk.max_minutes : "";
        var transitMins = wp.modes && wp.modes.transit ? wp.modes.transit.max_minutes : "";
        return '<div class="waypoint-card" data-idx="' + index + '">' +
            '<div class="wp-header"><input class="wp-name" value="' + esc(wp.name) + '"><span class="remove-wp" title="Remove">&times;</span></div>' +
            '<div class="wp-fields">' +
            '<div><label>Lat</label><input class="wp-lat" type="number" step="any" value="' + wp.lat + '"></div>' +
            '<div><label>Lng</label><input class="wp-lng" type="number" step="any" value="' + wp.lng + '"></div>' +
            '<div><label>Tier</label><select class="wp-tier">' +
            '<option value="strong"' + (wp.tier === "strong" ? " selected" : "") + '>Strong</option>' +
            '<option value="moderate"' + (wp.tier === "moderate" ? " selected" : "") + '>Moderate</option>' +
            '<option value="low"' + (wp.tier === "low" ? " selected" : "") + '>Low</option>' +
            '</select></div>' +
            '<div></div>' +
            '</div>' +
            '<div class="mode-row"><label>Walk max</label><input class="wp-walk-max" type="number" value="' + walkMins + '" placeholder="min"> min</div>' +
            '<div class="mode-row"><label>Transit max</label><input class="wp-transit-max" type="number" value="' + transitMins + '" placeholder="min"> min</div>' +
            '</div>';
    }

    function readConfigFromPanel() {
        function numOrNull(id) {
            var el = document.getElementById(id);
            if (!el) return null;
            var v = el.value.trim();
            return v === "" ? null : Number(v);
        }
        function strOrNull(id) {
            var el = document.getElementById(id);
            if (!el) return null;
            var v = el.value.trim();
            return v === "" ? null : v;
        }

        var filters = {
            rent: { min: numOrNull("filter-rent-min"), max: numOrNull("filter-rent-max") },
            bedrooms: { min: numOrNull("filter-bedrooms-min"), max: numOrNull("filter-bedrooms-max") },
            bathrooms: { min: numOrNull("filter-bathrooms-min"), max: numOrNull("filter-bathrooms-max") },
            sqft: { min: numOrNull("filter-sqft-min"), max: numOrNull("filter-sqft-max") },
            city: strOrNull("filter-city"),
            pets_allowed: (function () {
                var v = document.getElementById("filter-pets").value;
                if (v === "true") return true;
                if (v === "false") return false;
                return null;
            })()
        };

        // Read pref tiers from selects
        var prefTiers = sidePanel.querySelectorAll(".pref-tier");
        var prefs = [];
        for (var i = 0; i < prefTiers.length; i++) {
            var idx = parseInt(prefTiers[i].dataset.idx);
            if (config.preferences[idx]) {
                prefs.push({ name: config.preferences[idx].name, tier: prefTiers[i].value });
            }
        }

        // Read waypoints
        var wpCards = sidePanel.querySelectorAll(".waypoint-card");
        var waypoints = [];
        for (var w = 0; w < wpCards.length; w++) {
            var modes = {};
            var walkMax = wpCards[w].querySelector(".wp-walk-max").value;
            var transitMax = wpCards[w].querySelector(".wp-transit-max").value;
            if (walkMax) modes.walk = { max_minutes: parseInt(walkMax) };
            if (transitMax) modes.transit = { max_minutes: parseInt(transitMax) };
            if (!Object.keys(modes).length) modes.walk = { max_minutes: 30 };

            waypoints.push({
                name: wpCards[w].querySelector(".wp-name").value,
                lat: parseFloat(wpCards[w].querySelector(".wp-lat").value),
                lng: parseFloat(wpCards[w].querySelector(".wp-lng").value),
                tier: wpCards[w].querySelector(".wp-tier").value,
                modes: modes
            });
        }

        return { filters: filters, preferences: prefs, waypoints: waypoints };
    }

    function applyConfig() {
        var newConfig = readConfigFromPanel();
        var applyBtn = document.getElementById("apply-config-btn");
        applyBtn.disabled = true;
        applyBtn.textContent = "Applying...";

        // 1. Save config
        fetch("/api/config", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(newConfig)
        }).then(function () {
            // 2. Re-score
            return fetch("/api/config/apply", { method: "POST" });
        }).then(function () {
            config = newConfig;
            waypointsData = newConfig.waypoints;
            return fetchListingsAsync();
        }).then(function () {
            applyBtn.disabled = false;
            applyBtn.textContent = "Apply changes";
            renderMap();
        }).catch(function (err) {
            applyBtn.disabled = false;
            applyBtn.textContent = "Apply changes";
            alert("Failed to apply config: " + err);
        });
    }

    function fetchListingsAsync() {
        return fetch("/api/listings")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                allListings = data.listings;
                if (data.new_count > 0) {
                    newBannerText.textContent = data.new_count + " new listing" + (data.new_count === 1 ? "" : "s") + " since last scrape";
                    newBanner.classList.add("visible");
                }
                renderCards();
            });
    }
})();
