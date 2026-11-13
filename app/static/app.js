// ==========================================================================
// LPU Event Scheduler & Resource Conflict Resolution Engine - Client App
// ==========================================================================

let state = null;
let currentSchedule = null;

const $ = (id) => document.getElementById(id);

// Safe date formatter
const fmtSlot = (slotStr) => {
  try {
    const dt = new Date(slotStr);
    return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (e) {
    return slotStr;
  }
};

const fmtFull = (slotStr) => {
  try {
    const dt = new Date(slotStr);
    return dt.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch (e) {
    return slotStr;
  }
};

// API Helper with Session Authentication and CSRF Protection
async function api(path, options = {}) {
  const csrf = sessionStorage.getItem('lpu_csrf_token') || '';
  const headers = {
    'Content-Type': 'application/json',
    ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
    ...(options.headers || {}),
  };
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Network request failed' }));
    throw new Error(err.detail || 'Request failed');
  }
  return res.json();
}

// --------------------------------------------------------------------------
// Theme Switching (4 Reference Photo Modes)
// --------------------------------------------------------------------------
const quotesByTheme = {
  light: '"Turning Ideas into Events"',
  dark: '"Turning Ideas into Events"',
  cyber: '"Turning Ideas into Events"',
  emerald: '"Turning Ideas into Events"',
};

function initTheme() {
  const savedTheme = localStorage.getItem('lpu_theme') || 'light';
  setTheme(savedTheme);

  const modeBtn = $('modeToggle');
  const themeMenu = $('themeMenu');

  modeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    themeMenu.classList.toggle('hidden');
  });

  document.addEventListener('click', () => {
    themeMenu.classList.add('hidden');
  });

  document.querySelectorAll('.theme-option').forEach((opt) => {
    opt.addEventListener('click', () => {
      const theme = opt.dataset.theme;
      setTheme(theme);
    });
  });
}

function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('lpu_theme', theme);

  document.querySelectorAll('.theme-option').forEach((opt) => {
    opt.classList.toggle('active', opt.dataset.theme === theme);
  });

  const quoteEl = $('heroQuote');
  if (quoteEl) quoteEl.textContent = quotesByTheme[theme] || quotesByTheme.light;

  const iconEl = $('themeIcon');
  const textEl = $('themeText');
  if (theme === 'light') {
    if (iconEl) iconEl.textContent = '☀️';
    if (textEl) textEl.textContent = 'Classic Light';
  } else if (theme === 'dark') {
    if (iconEl) iconEl.textContent = '🌙';
    if (textEl) textEl.textContent = 'Midnight Blue';
  } else if (theme === 'cyber') {
    if (iconEl) iconEl.textContent = '⚡';
    if (textEl) textEl.textContent = 'Cyber Purple';
  } else if (theme === 'emerald') {
    if (iconEl) iconEl.textContent = '🌿';
    if (textEl) textEl.textContent = 'Emerald Eco';
  }
}

// --------------------------------------------------------------------------
// State Rendering & Stat Cards
// --------------------------------------------------------------------------
function renderState() {
  if (!state) return;

  // 1. Update 5 Stat Cards
  $('statDepts').textContent = state.departments ? state.departments.length : 0;
  $('statEvents').textContent = state.events ? state.events.length : 0;
  $('statVenues').textContent = state.venues ? state.venues.length : 0;
  $('statStaff').textContent = state.staff ? state.staff.length : 0;
  $('statEquipment').textContent = state.equipment ? state.equipment.length : 0;

  const deptMap = Object.fromEntries((state.departments || []).map((d) => [d.id, d.name]));

  // 2. Render Events Table in Directory
  const tbody = $('eventsTableBody');
  tbody.innerHTML = (state.events || [])
    .map((e) => {
      const riskBadge = `<span class="badge risk-${(e.ml_conflict_risk || 'low').toLowerCase()}">${e.ml_conflict_risk || 'Low'} Risk</span>`;
      return `
      <tr>
        <td><strong>${e.name}</strong></td>
        <td>${deptMap[e.department_id] || e.department_id}</td>
        <td>${e.duration_slots} hr(s)</td>
        <td>${e.expected_attendance} <small class="text-muted">(pred: ${e.ml_predicted_attendance || e.expected_attendance})</small></td>
        <td>${e.required_venue_type || 'Any suitable'}</td>
        <td><span class="badge ${e.priority}">${e.priority}</span></td>
        <td>${riskBadge}</td>
        <td>
          <button class="btn btn-sm btn-secondary" onclick="inspectEventFromDirectory(${e.id})">Explain</button>
          <button class="btn btn-sm btn-secondary" style="color:#dc2626;" onclick="deleteEvent(${e.id})">✕</button>
        </td>
      </tr>
    `;
    })
    .join('');

  // 3. Render Venues Table in Directory
  $('venuesTableBody').innerHTML = (state.venues || [])
    .map((v) => `
      <tr>
        <td><strong>${v.name}</strong></td>
        <td><span class="badge normal">${v.venue_type}</span></td>
        <td>${v.capacity} seats</td>
        <td>${v.location || 'Academic Block'}</td>
        <td>${(v.available_slots || []).length} slots open</td>
      </tr>
    `)
    .join('');

  // 4. Render Staff Table
  $('staffTableBody').innerHTML = (state.staff || [])
    .map((s) => `
      <tr>
        <td><strong>${s.name}</strong></td>
        <td>${s.role || 'Faculty'}</td>
        <td>${(s.skills || []).join(', ') || 'Academic Supervision'}</td>
        <td>${(s.available_slots || []).length} active slots</td>
      </tr>
    `)
    .join('');

  // 5. Render Equipment Table
  $('equipmentTableBody').innerHTML = (state.equipment || [])
    .map((eq) => `
      <tr>
        <td><strong>${eq.name}</strong></td>
        <td><span class="badge normal">${eq.category || 'AV'}</span></td>
        <td><strong>${eq.quantity}</strong> unit(s)</td>
      </tr>
    `)
    .join('');

  // 6. Populate Select Dropdowns
  populateDropdowns();
}

function populateDropdowns() {
  if (!state) return;

  // Event modal department select
  const deptSelect = $('eventDeptSelect');
  if (deptSelect) {
    deptSelect.innerHTML = (state.departments || [])
      .map((d) => `<option value="${d.id}">${d.name}</option>`)
      .join('');
  }

  // Explain dropdown
  const explainSelect = $('explainEventSelect');
  if (explainSelect) {
    explainSelect.innerHTML = (state.events || [])
      .map((e) => `<option value="${e.id}">${e.name} (${e.priority})</option>`)
      .join('');
  }

  // What-If event select
  const whatIfEvSelect = $('whatIfEventSelect');
  if (whatIfEvSelect) {
    whatIfEvSelect.innerHTML = (state.events || [])
      .map((e) => `<option value="${e.id}">${e.name}</option>`)
      .join('');
  }

  // What-If venue select
  const whatIfVenueSelect = $('whatIfVenueSelect');
  if (whatIfVenueSelect) {
    whatIfVenueSelect.innerHTML = (state.venues || [])
      .map((v) => `<option value="${v.id}">${v.name} (Cap: ${v.capacity})</option>`)
      .join('');
  }

  // What-If unavailable venue select
  const whatIfUnavailSelect = $('whatIfUnavailableVenueSelect');
  if (whatIfUnavailSelect) {
    whatIfUnavailSelect.innerHTML = '<option value="">None (All Venues Available)</option>' +
      (state.venues || [])
        .map((v) => `<option value="${v.id}">${v.name} (Cap: ${v.capacity})</option>`)
        .join('');
  }

  // What-If time shift event select
  const whatIfTimeShiftEvSelect = $('whatIfTimeShiftEventSelect');
  if (whatIfTimeShiftEvSelect) {
    whatIfTimeShiftEvSelect.innerHTML = '<option value="">-- None (Keep Original Times) --</option>' +
      (state.events || [])
        .map((e) => `<option value="${e.id}">${e.name}</option>`)
        .join('');
  }

  // Timetable filter department select
  const filterDeptSelect = $('filterDeptSelect');
  if (filterDeptSelect) {
    const prevVal = filterDeptSelect.value || 'all';
    filterDeptSelect.innerHTML = '<option value="all">All Departments</option>' +
      (state.departments || [])
        .map((d) => `<option value="${d.id}">${d.name} (${d.code || d.name})</option>`)
        .join('');
    filterDeptSelect.value = prevVal;
  }

  // Timetable filter venue select
  const filterVenueSelect = $('filterVenueSelect');
  if (filterVenueSelect) {
    const prevVal = filterVenueSelect.value || 'all';
    filterVenueSelect.innerHTML = '<option value="all">All Venues</option>' +
      (state.venues || [])
        .map((v) => `<option value="${v.id}">${v.name} (Cap: ${v.capacity})</option>`)
        .join('');
    filterVenueSelect.value = prevVal;
  }
}

// --------------------------------------------------------------------------
// Date-Wise Interactive Timetable Engine
// --------------------------------------------------------------------------

let timetableState = {
  selectedDate: '2026-10-05', // Default starting demo date
  viewMode: 'day',            // 'day' | 'week' | 'month'
  intervalMinutes: 60,        // 60 (hourly default) | 30 | 15 minutes
  fitScreen: false,           // Auto fit to screen without horizontal scrolling
  filters: {
    deptId: 'all',
    venueId: 'all',
    priority: 'all',
    search: '',
  },
};

// Parse ISO slot string "YYYY-MM-DDTHH:MM" safely and deterministically
function parseSlotIso(slotStr) {
  if (!slotStr) return null;
  try {
    const s = String(slotStr).trim();
    let datePart = '';
    let timePart = '';
    if (s.includes('T')) {
      const parts = s.split('T');
      datePart = parts[0];
      timePart = parts[1];
    } else if (s.includes(' ')) {
      const parts = s.split(' ');
      datePart = parts[0];
      timePart = parts[1];
    } else {
      const dt = new Date(s);
      if (isNaN(dt.getTime())) return null;
      datePart = dt.toISOString().split('T')[0];
      timePart = `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`;
    }
    if (!timePart) timePart = '00:00';
    const timeParts = timePart.split(':');
    const hour = parseInt(timeParts[0], 10) || 0;
    const minute = parseInt(timeParts[1], 10) || 0;
    return { dateStr: datePart, hour, minute, totalMin: hour * 60 + minute };
  } catch (e) {
    console.warn('Error parsing slot string:', slotStr, e);
    return null;
  }
}

// Get clean interval metrics for any assignment
function getAssignmentInterval(asgn) {
  if (!asgn || !asgn.slots || asgn.slots.length === 0) return null;
  const first = parseSlotIso(asgn.slots[0]);
  if (!first) return null;

  const evObj = (state && state.events ? state.events.find((e) => e.id === asgn.event_id) : null) || {};

  // Check if slot step is determinable from consecutive slot timestamps
  let slotStepMin = 60; // default 1 hour per slot in this CSP system
  if (asgn.slots.length > 1) {
    const second = parseSlotIso(asgn.slots[1]);
    if (second && second.dateStr === first.dateStr && second.totalMin > first.totalMin) {
      slotStepMin = second.totalMin - first.totalMin;
    }
  }

  // Determine total duration in minutes
  let durationMinutes = 0;
  if (asgn.duration_minutes) {
    durationMinutes = asgn.duration_minutes;
  } else if (asgn.start_time && asgn.end_time) {
    const parseHM = (t) => {
      const parts = String(t).split(':').map(Number);
      return parts[0] * 60 + (parts[1] || 0);
    };
    const sMin = parseHM(asgn.start_time);
    const eMin = parseHM(asgn.end_time);
    if (eMin > sMin) durationMinutes = eMin - sMin;
  } else if (evObj.duration_minutes) {
    durationMinutes = evObj.duration_minutes;
  } else if (asgn.duration_slots) {
    durationMinutes = Math.round(asgn.duration_slots * 60);
  } else if (evObj.duration_slots) {
    durationMinutes = Math.round(evObj.duration_slots * 60);
  } else {
    durationMinutes = asgn.slots.length * slotStepMin;
  }

  if (durationMinutes <= 0) durationMinutes = 60;

  const startHour = first.hour;
  const startMin = first.minute;
  const startTotalMin = first.totalMin;
  const endTotalMin = startTotalMin + durationMinutes;
  const endHour = Math.floor(endTotalMin / 60);
  const endMin = endTotalMin % 60;
  const dateStr = first.dateStr;
  const durationHours = durationMinutes / 60;

  const pad2 = (n) => String(n).padStart(2, '0');
  const startTimeStr = `${pad2(startHour)}:${pad2(startMin)}`;
  const endTimeStr = `${pad2(endHour)}:${pad2(endMin)}`;

  const durationLabel = durationMinutes < 60
    ? `${durationMinutes}m`
    : (durationMinutes % 60 === 0 ? `${durationHours}h` : `${durationHours.toFixed(1)}h`);

  return {
    dateStr,
    startHour,
    startMin,
    endHour,
    endMin,
    startTimeStr,
    endTimeStr,
    durationHours,
    durationMinutes,
    durationLabel,
    startTotalMin,
    endTotalMin,
  };
}

// Parse "YYYY-MM-DD" into a Date object at UTC noon (timezone-safe and immunity from midnight DST rollover)
function parseDateStr(dateStr) {
  if (!dateStr) return new Date();
  const [y, m, d] = String(dateStr).split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d, 12, 0, 0));
}

// Format Date object into "YYYY-MM-DD" using UTC methods
function formatDateIso(d) {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// Generate the 7 dates (Monday through Sunday) for the week containing dateStr
function getWeekDates(dateStr) {
  const [y, m, d] = String(dateStr).split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d, 12, 0, 0));
  const dayOfWeek = dt.getUTCDay(); // 0 is Sunday, 1 is Monday ... 6 is Saturday
  const diffToMonday = dayOfWeek === 0 ? -6 : 1 - dayOfWeek;
  const mondayUtcMs = dt.getTime() + (diffToMonday * 86400000);

  const dates = [];
  for (let i = 0; i < 7; i++) {
    const cur = new Date(mondayUtcMs + (i * 86400000));
    dates.push(formatDateIso(cur));
  }
  return dates;
}

// Accurate venue-overlap conflict detection on a specific date [start, end)
function detectVenueConflicts(assignmentsOnDate) {
  const conflictEventIds = new Set();
  const conflictDetails = {};

  for (let i = 0; i < assignmentsOnDate.length; i++) {
    for (let j = i + 1; j < assignmentsOnDate.length; j++) {
      const a = assignmentsOnDate[i];
      const b = assignmentsOnDate[j];

      // Conflicts apply when events are assigned to the SAME venue
      if (a.venue_id === b.venue_id) {
        const aInt = getAssignmentInterval(a);
        const bInt = getAssignmentInterval(b);
        if (aInt && bInt) {
          // Half-open interval overlap: not (a.end <= b.start || b.end <= a.start)
          const overlaps = !(aInt.endTotalMin <= bInt.startTotalMin || bInt.endTotalMin <= aInt.startTotalMin);
          if (overlaps) {
            conflictEventIds.add(a.event_id);
            conflictEventIds.add(b.event_id);
            conflictDetails[a.event_id] = `Overlaps with "${b.event_name}" (${bInt.startTimeStr}–${bInt.endTimeStr}) in ${a.venue_name}`;
            conflictDetails[b.event_id] = `Overlaps with "${a.event_name}" (${aInt.startTimeStr}–${aInt.endTimeStr}) in ${b.venue_name}`;
          }
        }
      }
    }
  }

  return { conflictEventIds, conflictDetails };
}

// Apply active UI filters (department, venue, priority, title search)
function filterAssignments(assignments) {
  return (assignments || []).filter((a) => {
    const ev = (state && state.events ? state.events.find((e) => e.id === a.event_id) : null);
    const evPriority = (ev && ev.priority) ? ev.priority.toLowerCase() : '';
    const dept = ev && state && state.departments ? state.departments.find((d) => d.id === ev.department_id) : null;
    const deptName = a.department_name || (dept ? dept.name : '');

    // 1. Department filter
    if (timetableState.filters.deptId !== 'all') {
      if (ev && String(ev.department_id) !== String(timetableState.filters.deptId)) return false;
      if (!ev && deptName && !deptName.includes(timetableState.filters.deptId)) return false;
    }

    // 2. Venue filter
    if (timetableState.filters.venueId !== 'all') {
      if (String(a.venue_id) !== String(timetableState.filters.venueId)) return false;
    }

    // 3. Priority filter
    if (timetableState.filters.priority !== 'all') {
      if (evPriority !== timetableState.filters.priority.toLowerCase()) return false;
    }

    // 4. Search query
    if (timetableState.filters.search) {
      const q = timetableState.filters.search.toLowerCase();
      const match = (a.event_name && a.event_name.toLowerCase().includes(q)) ||
                    (deptName && deptName.toLowerCase().includes(q)) ||
                    (a.venue_name && a.venue_name.toLowerCase().includes(q)) ||
                    evPriority.includes(q);
      if (!match) return false;
    }

    return true;
  });
}

// Update the Top Navigation and Filter UI state
function updateTimetableUIControls() {
  // 1. View Buttons
  ['day', 'week', 'month'].forEach((m) => {
    const btn = $(`view${m.charAt(0).toUpperCase() + m.slice(1)}Btn`);
    if (btn) btn.classList.toggle('active', timetableState.viewMode === m);
  });

  // 2. Interval selector visibility (only relevant in Day View)
  const intervalGroup = $('intervalSelectorGroup');
  if (intervalGroup) {
    intervalGroup.style.display = timetableState.viewMode === 'day' ? 'flex' : 'none';
  }
  const intervalSelect = $('intervalSelect');
  if (intervalSelect && timetableState.intervalMinutes) {
    intervalSelect.value = String(timetableState.intervalMinutes);
  }

  // 3. Date heading text
  const heading = $('currentDateHeading');
  const d = parseDateStr(timetableState.selectedDate);

  if (timetableState.viewMode === 'day') {
    if (heading) {
      heading.textContent = d.toLocaleDateString([], {
        timeZone: 'UTC',
        weekday: 'long',
        month: 'long',
        day: '2-digit',
        year: 'numeric',
      });
    }
  } else if (timetableState.viewMode === 'week') {
    const weekDates = getWeekDates(timetableState.selectedDate);
    const startD = parseDateStr(weekDates[0]);
    const endD = parseDateStr(weekDates[6]);
    const startStr = startD.toLocaleDateString([], { timeZone: 'UTC', month: 'short', day: 'numeric' });
    const endStr = endD.toLocaleDateString([], { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' });
    if (heading) heading.textContent = `Week: ${startStr} – ${endStr}`;
  } else if (timetableState.viewMode === 'month') {
    if (heading) {
      heading.textContent = d.toLocaleDateString([], { timeZone: 'UTC', month: 'long', year: 'numeric' });
    }
  }

  // 4. Date Picker Input Value
  const datePicker = $('timetableDatePicker');
  if (datePicker && datePicker.value !== timetableState.selectedDate) {
    datePicker.value = timetableState.selectedDate;
  }

  // 5. Active filter badge
  const isFiltered = timetableState.filters.deptId !== 'all' ||
                     timetableState.filters.venueId !== 'all' ||
                     timetableState.filters.priority !== 'all' ||
                     Boolean(timetableState.filters.search);
  const filterBadge = $('activeFilterBadge');
  if (filterBadge) filterBadge.classList.toggle('hidden', !isFiltered);

  // 6. Synchronize filter DOM inputs with timetableState
  const filterSearch = $('filterSearchInput');
  if (filterSearch && filterSearch.value !== timetableState.filters.search) {
    filterSearch.value = timetableState.filters.search || '';
  }
  const filterDept = $('filterDeptSelect');
  if (filterDept && filterDept.value !== timetableState.filters.deptId) {
    filterDept.value = timetableState.filters.deptId;
  }
  const filterVenue = $('filterVenueSelect');
  if (filterVenue && filterVenue.value !== timetableState.filters.venueId) {
    filterVenue.value = timetableState.filters.venueId;
  }
  const filterPriority = $('filterPrioritySelect');
  if (filterPriority && filterPriority.value !== timetableState.filters.priority) {
    filterPriority.value = timetableState.filters.priority;
  }
}

// Quick switch to Day View for a given date (used from Week/Month clicks)
function switchToDay(dateStr) {
  timetableState.selectedDate = dateStr;
  timetableState.viewMode = 'day';
  updateTimetableUIControls();
  if (currentSchedule) renderTimetable(currentSchedule);
}

// Date navigation handler (Previous / Next)
function navigateDate(delta) {
  const [y, m, d] = timetableState.selectedDate.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d, 12, 0, 0));
  if (timetableState.viewMode === 'day') {
    dt.setUTCDate(dt.getUTCDate() + delta);
  } else if (timetableState.viewMode === 'week') {
    dt.setUTCDate(dt.getUTCDate() + delta * 7);
  } else if (timetableState.viewMode === 'month') {
    dt.setUTCMonth(dt.getUTCMonth() + delta);
  }
  timetableState.selectedDate = formatDateIso(dt);
  updateTimetableUIControls();
  if (currentSchedule) renderTimetable(currentSchedule);
}

// Main Timetable Dispatcher
function renderTimetable(result) {
  currentSchedule = result;

  // 1. Stats Bar
  if ($('schedCount')) $('schedCount').textContent = result.assignments.length;
  if ($('unresCount')) $('unresCount').textContent = result.unresolved.length;
  if ($('softCostVal')) $('softCostVal').textContent = result.total_soft_cost;
  if ($('solverTimeVal')) $('solverTimeVal').textContent = `${result.solver_time_ms}ms`;
  if ($('nodesCountVal')) $('nodesCountVal').textContent = result.explored_nodes;

  // 2. Validate current selected date against assignments
  // In Week View, check if current week has assignments; in Day View check if date has assignments
  if ((result.assignments || []).length > 0) {
    if (timetableState.viewMode === 'week') {
      const weekDates = getWeekDates(timetableState.selectedDate);
      const weekHasAssignments = (result.assignments || []).some((a) => {
        const info = getAssignmentInterval(a);
        return info && weekDates.includes(info.dateStr);
      });
      if (!weekHasAssignments) {
        const firstInfo = getAssignmentInterval(result.assignments[0]);
        if (firstInfo && firstInfo.dateStr) {
          timetableState.selectedDate = firstInfo.dateStr;
        }
      }
    } else {
      const hasOnCurrentDate = (result.assignments || []).some((a) => {
        const info = getAssignmentInterval(a);
        return info && info.dateStr === timetableState.selectedDate;
      });
      if (!hasOnCurrentDate) {
        const firstInfo = getAssignmentInterval(result.assignments[0]);
        if (firstInfo && firstInfo.dateStr) {
          timetableState.selectedDate = firstInfo.dateStr;
        }
      }
    }
  }

  // 3. Update top UI Controls
  updateTimetableUIControls();

  // 4. Render the active view mode
  if (timetableState.viewMode === 'day') {
    renderDayView(result);
  } else if (timetableState.viewMode === 'week') {
    renderWeekView(result);
  } else if (timetableState.viewMode === 'month') {
    renderMonthView(result);
  }

  // 5. Render Unresolved Clashes Box
  const unresBox = $('unresolvedBox');
  const unresList = $('unresolvedList');

  if (result.unresolved && result.unresolved.length > 0) {
    unresBox.classList.remove('hidden');
    unresList.innerHTML = result.unresolved
      .map((u) => {
        const priorityClass = (u.priority || 'normal').toLowerCase();
        const evObj = (state && state.events ? state.events.find((e) => e.id === u.event_id) : null) || {};
        const attendance = evObj.expected_attendance || u.expected_attendance || u.attendance || '—';
        // Best available venue capacity: look up from state.venues largest capacity
        const largestCap = (state && state.venues && state.venues.length > 0)
          ? Math.max(...state.venues.map(v => v.capacity))
          : 300;
        const reasonText = (u.reasons && u.reasons.length > 0)
          ? u.reasons.join(' • ')
          : (u.reason || 'No suitable venue or time slot found within constraints.');
        const altHtml = u.suggestion
          ? `<div style="margin-top:8px; padding:8px 12px; background:rgba(3,105,161,0.08); border-left:3px solid #0284c7; border-radius:6px; font-size:0.8rem; color:#0369a1; font-weight:600;">💡 Suggestion: ${u.suggestion.reason}</div>`
          : '';
        return `
        <div class="conflict-item">
          <div class="conflict-item-grid">
            <div class="conflict-item-name">⚠️ ${u.event_name}</div>
            <div class="conflict-item-badges">
              <span class="conflict-priority-badge ${priorityClass}">${(u.priority || 'NORMAL').toUpperCase()}</span>
            </div>
            <div class="conflict-reason-text">${reasonText}</div>
          </div>
          <div class="conflict-capacity-text">
            <span>👥 Expected Attendance: <strong>${attendance} seats</strong></span>
            <span style="opacity:0.6;">•</span>
            <span>🏛️ Largest Available Venue: <strong>${largestCap} seats</strong></span>
            ${evObj.required_venue_type ? `<span style="opacity:0.6;">•</span><span>Required Type: <strong>${evObj.required_venue_type}</strong></span>` : ''}
          </div>
          ${altHtml}
        </div>
      `;
      })
      .join('');
  } else {
    unresBox.classList.add('hidden');
  }

  // Auto update Explain AI tab
  if (result.assignments.length > 0) {
    inspectEvent(result.assignments[0].event_id);
  } else if (result.unresolved.length > 0) {
    inspectEvent(result.unresolved[0].event_id);
  }
}

// --------------------------------------------------------------------------
// 1. DAY VIEW: Venues as rows with Continuous Multi-Hour Event Cards
// --------------------------------------------------------------------------
function renderDayView(result) {
  const container = $('timetableGrid');
  if (!container) return;
  try {
    const selDate = timetableState.selectedDate;
    const interval = timetableState.intervalMinutes || 60;
    const DAY_START_HOUR = 9;  // 09:00
    const DAY_END_HOUR = 18;   // 18:00
    const totalSlots = ((DAY_END_HOUR - DAY_START_HOUR) * 60) / interval;
    const showHeatmap = $('heatmapToggle') && $('heatmapToggle').checked;

    // Filter assignments strictly for this single selected date
    const assignmentsOnDate = (result.assignments || []).filter((a) => {
      const info = getAssignmentInterval(a);
      return info && info.dateStr === selDate;
    });

    // Accurate conflict detection for same-venue overlaps
    const { conflictEventIds, conflictDetails } = detectVenueConflicts(assignmentsOnDate);

    // Apply search/dept/venue/priority filters
    const filteredAssignments = filterAssignments(assignmentsOnDate);

    // Group by venue
    const byVenue = {};
    filteredAssignments.forEach((a) => {
      if (!byVenue[a.venue_id]) byVenue[a.venue_id] = [];
      byVenue[a.venue_id].push(a);
    });

    // Determine venues to display (with fallback if state.venues not yet loaded)
    let venuesToDisplay = (state && state.venues && state.venues.length > 0) ? [...state.venues] : [];
    if (venuesToDisplay.length === 0 && (result.assignments || []).length > 0) {
      const seenVenues = new Map();
      (result.assignments || []).forEach((a) => {
        if (!seenVenues.has(a.venue_id)) {
          seenVenues.set(a.venue_id, {
            id: a.venue_id,
            name: a.venue_name || `Venue ${a.venue_id}`,
            capacity: 100,
            venue_type: 'Campus Facility',
            location: 'Academic Campus',
          });
        }
      });
      venuesToDisplay = Array.from(seenVenues.values());
    }

    if (timetableState.filters.venueId !== 'all') {
      venuesToDisplay = venuesToDisplay.filter((v) => String(v.id) === String(timetableState.filters.venueId));
    }

    // Compact venue column (175px) and balanced responsive slot widths
    const venueColWidth = 175;
    const isFitScreen = Boolean(timetableState.fitScreen);
    // Min slot width: when fitScreen is on: 0 (fits viewport); otherwise 46px (30m), 32px (15m), 75px (60m)
    const slotMinWidth = isFitScreen ? 0 : (interval === 15 ? 32 : (interval === 30 ? 46 : 75));
    const gridTemplateCols = `${venueColWidth}px repeat(${totalSlots}, minmax(${slotMinWidth}px, 1fr))`;
    const trackTemplateCols = `repeat(${totalSlots}, minmax(${slotMinWidth}px, 1fr))`;

    // Build Day View Grid Header
    let html = `<div class="day-grid-header" style="grid-template-columns: ${gridTemplateCols};">`;
    html += `<div class="day-header-venue-col">VENUE / LOCATION</div>`;

    for (let i = 0; i < totalSlots; i++) {
      const totalMin = (DAY_START_HOUR * 60) + (i * interval);
      const h = Math.floor(totalMin / 60);
      const m = totalMin % 60;
      const timeLabel = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
      html += `<div class="day-header-slot">${timeLabel}</div>`;
    }
    html += `</div>`;

    if (venuesToDisplay.length === 0) {
      html += `<div class="timetable-empty-state"><h4>No venues match the active filter</h4></div>`;
      container.innerHTML = html;
      return;
    }

    // Render Each Venue Row
    venuesToDisplay.forEach((venue) => {
      const venueEvents = byVenue[venue.id] || [];
      const eventCountText = venueEvents.length === 1 ? '1 event' : `${venueEvents.length} events`;

      // Sort venue events by start time
      venueEvents.sort((a, b) => {
        const ia = getAssignmentInterval(a);
        const ib = getAssignmentInterval(b);
        return (ia ? ia.startTotalMin : 0) - (ib ? ib.startTotalMin : 0);
      });

      // Calculate row tracks for overlapping events so no conflict is visually covered
      const rowTrackEnds = [];
      const eventRowMap = new Map();
      venueEvents.forEach((asgn) => {
        const info = getAssignmentInterval(asgn);
        if (!info) return;
        let placed = false;
        for (let r = 0; r < rowTrackEnds.length; r++) {
          if (info.startTotalMin >= rowTrackEnds[r]) {
            eventRowMap.set(asgn.event_id, r + 1);
            rowTrackEnds[r] = info.endTotalMin;
            placed = true;
            break;
          }
        }
        if (!placed) {
          rowTrackEnds.push(info.endTotalMin);
          eventRowMap.set(asgn.event_id, rowTrackEnds.length);
        }
      });
      const maxRowsInVenue = Math.max(1, rowTrackEnds.length);

      html += `<div class="day-venue-row" style="grid-template-columns: ${venueColWidth}px 1fr;">`;

      // Left Venue Label & Metadata (Compact & clear)
      const venueTypeTag = venue.venue_type ? venue.venue_type.toLowerCase() : 'general';
      html += `
        <div class="day-venue-info">
          <div class="day-venue-title" title="${venue.name}">${venue.name}</div>
          <div class="day-venue-meta">Cap: <strong>${venue.capacity}</strong> &nbsp;•&nbsp; <span class="day-venue-tag tag-${venueTypeTag}">${venue.venue_type}</span></div>
          <div class="day-venue-location">${venue.location || 'Academic Block'}</div>
        </div>
      `;

      // Right Timeline Grid Track (Compact row heights: minmax(90px, auto))
      html += `<div class="day-timeline-track" style="grid-template-columns: ${trackTemplateCols}; grid-template-rows: repeat(${maxRowsInVenue}, minmax(90px, auto));">`;

      // Background interval cells & heatmap (stretching across all sub-rows)
      for (let i = 1; i <= totalSlots; i++) {
        const slotMin = (DAY_START_HOUR * 60) + ((i - 1) * interval);
        const slotH = Math.floor(slotMin / 60);
        let heatmapClass = '';
        if (showHeatmap) {
          if (slotH === 10 || slotH === 11 || slotH === 14) heatmapClass = 'heatmap-2';
          else if (slotH === 9 || slotH === 15) heatmapClass = 'heatmap-1';
        }
        html += `<div class="day-bg-slot ${heatmapClass}" style="grid-column: ${i} / ${i + 1}; grid-row: 1 / ${maxRowsInVenue + 1};"></div>`;
      }

      // Render each event as ONE continuous card spanning its complete duration
      venueEvents.forEach((asgn) => {
        const info = getAssignmentInterval(asgn);
        if (!info) return;

        // Required minute-based calculations:
        // startOffset = (eventStartMinutes - gridStartMinutes) / intervalMinutes
        // endOffset = (eventEndMinutes - gridStartMinutes) / intervalMinutes
        // startColumn = startOffset + 1
        // endColumn = endOffset + 1
        const gridStartMinutes = DAY_START_HOUR * 60; // 540 (09:00)
        const gridEndMinutes = DAY_END_HOUR * 60;     // 1080 (18:00)
        const eventStartMinutes = info.startTotalMin;
        const eventEndMinutes = info.endTotalMin;

        // Skip events completely outside operational hours (e.g., before 09:00 or after 18:00)
        if (eventEndMinutes <= gridStartMinutes || eventStartMinutes >= gridEndMinutes) return;

        const clampedStartMinutes = Math.max(gridStartMinutes, Math.min(gridEndMinutes, eventStartMinutes));
        const clampedEndMinutes = Math.max(clampedStartMinutes + interval, Math.min(gridEndMinutes, eventEndMinutes));

        const startOffset = (clampedStartMinutes - gridStartMinutes) / interval;
        const endOffset = (clampedEndMinutes - gridStartMinutes) / interval;

        const startColumn = Math.max(1, Math.round(startOffset) + 1);
        const endColumn = Math.min(totalSlots + 1, Math.max(startColumn + 1, Math.round(endOffset) + 1));
        const colSpan = Math.max(1, endColumn - startColumn);
        const rowNum = eventRowMap.get(asgn.event_id) || 1;

        // Determine Department Color
        let deptClass = 'dept-cse';
        if (asgn.department_name && asgn.department_name.includes('Elec')) deptClass = 'dept-ece';
        else if (asgn.department_name && asgn.department_name.includes('Bus')) deptClass = 'dept-mba';

        // Event details
        const evObj = (state && state.events ? state.events.find((e) => e.id === asgn.event_id) : null) || {};
        const priority = (evObj.priority || 'normal').toLowerCase();
        const attendance = evObj.expected_attendance || asgn.ml_predicted_attendance || venue.capacity;
        const deptShort = asgn.department_name ? asgn.department_name.split(' ')[0] : 'Dept';

        // Conflict highlighting (red warning border)
        const isConflict = conflictEventIds.has(asgn.event_id);
        const conflictClass = isConflict ? 'conflict-clash' : '';
        const conflictBadgeHtml = isConflict
          ? `<div class="conflict-warning-tag">⚠️ Overlap: ${conflictDetails[asgn.event_id] || 'Venue double-booking'}</div>`
          : '';

        // Span-based modifier class for compact/full CSS typography sizing
        const spanClass = colSpan === 1 ? 'card-span-1' : (colSpan === 2 ? 'card-span-2' : 'card-span-multi');

        // For 1-column cards: stack title + badge vertically; for wider cards: side by side
        const topRowHtml = colSpan === 1
          ? `<div class="card-top-row">
               <span class="card-title" title="${asgn.event_name}">${asgn.event_name}</span>
               <span class="card-priority-badge ${priority}">${priority.toUpperCase()}</span>
             </div>`
          : `<div class="card-top-row">
               <span class="card-title" title="${asgn.event_name}">${asgn.event_name}</span>
               <span class="card-priority-badge ${priority}">${priority.toUpperCase()}</span>
             </div>`;

        html += `
          <div class="continuous-event-card ${deptClass} ${conflictClass} ${spanClass}"
               style="grid-column: ${startColumn} / span ${colSpan}; grid-row: ${rowNum}; width: 100%; max-width: 100%; box-sizing: border-box; justify-self: stretch;"
               onclick="inspectEventFromDirectory(${asgn.event_id})"
               title="${asgn.event_name} (${info.startTimeStr}–${info.endTimeStr}, ${info.durationLabel}) • Click for Explainable AI"
               data-event-id="${asgn.event_id}"
               data-start-time="${info.startTimeStr}"
               data-end-time="${info.endTimeStr}"
               data-start-min="${eventStartMinutes}"
               data-end-min="${eventEndMinutes}"
               data-duration-min="${info.durationMinutes}"
               data-start-col="${startColumn}"
               data-end-col="${endColumn}"
               data-span-cols="${colSpan}">
            ${topRowHtml}
            <div class="card-time-row">
              🕒 <strong>${info.startTimeStr} – ${info.endTimeStr} (${info.durationLabel})</strong>
            </div>
            <div class="card-meta-row">
              <span class="card-meta-chip">👥 ${attendance}</span>
              <span class="card-meta-chip">${deptShort}</span>
              <span class="card-meta-chip">+${asgn.soft_cost}</span>
            </div>
            ${conflictBadgeHtml}
          </div>
        `;
      });

      html += `</div>`; // end day-timeline-track
      html += `</div>`; // end day-venue-row
    });

    // Empty state if no events on this date
    if (assignmentsOnDate.length === 0) {
      html += `
        <div class="timetable-empty-state">
          <h4>No events scheduled on ${parseDateStr(selDate).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}</h4>
          <p>Use the date navigation above or click <strong>"Demo (Oct 5)"</strong> to view scheduled university sessions.</p>
        </div>
      `;
    }

    container.innerHTML = html;
  } catch (err) {
    console.error('Error rendering Day View:', err);
    container.innerHTML = `<div class="timetable-empty-state"><h4 style="color:#ef4444;">Timetable Render Error: ${err.message}</h4></div>`;
  }
}

// --------------------------------------------------------------------------
// 2. WEEK VIEW: All Dates in the Selected Week with Event Cards
// --------------------------------------------------------------------------
function renderWeekView(result) {
  const container = $('timetableGrid');
  const weekDates = getWeekDates(timetableState.selectedDate);

  let html = `<div class="week-view-grid">`;

  weekDates.forEach((dateStr) => {
    const isActiveDay = dateStr === timetableState.selectedDate;
    const dateObj = parseDateStr(dateStr);
    const dayName = dateObj.toLocaleDateString([], { timeZone: 'UTC', weekday: 'short' });
    const dayNum = dateObj.toLocaleDateString([], { timeZone: 'UTC', month: 'short', day: 'numeric' });

    // Assignments on this specific date
    const onDate = (result.assignments || []).filter((a) => {
      const info = getAssignmentInterval(a);
      return info && info.dateStr === dateStr;
    });

    const { conflictEventIds } = detectVenueConflicts(onDate);
    const filtered = filterAssignments(onDate);

    html += `<div class="week-day-col">`;
    html += `
      <div class="week-day-header ${isActiveDay ? 'active-day' : ''}" onclick="switchToDay('${dateStr}')" title="Click to open Day View for this date">
        <div class="week-day-name">${dayName}</div>
        <div class="week-day-date">${dayNum}</div>
        <span class="week-day-badge">${filtered.length} event${filtered.length === 1 ? '' : 's'}</span>
      </div>
      <div class="week-day-events">
    `;

    if (filtered.length === 0) {
      html += `<div class="week-empty-day">No events scheduled</div>`;
    } else {
      // Sort by start time
      filtered.sort((a, b) => {
        const ia = getAssignmentInterval(a);
        const ib = getAssignmentInterval(b);
        return (ia ? ia.startTotalMin : 0) - (ib ? ib.startTotalMin : 0);
      });

      filtered.forEach((asgn) => {
        const info = getAssignmentInterval(asgn);
        if (!info) return;

        let deptClass = 'dept-cse';
        if (asgn.department_name && asgn.department_name.includes('Elec')) deptClass = 'dept-ece';
        else if (asgn.department_name && asgn.department_name.includes('Bus')) deptClass = 'dept-mba';

        const evObj = (state && state.events ? state.events.find((e) => e.id === asgn.event_id) : null) || {};
        const priority = evObj.priority || 'normal';
        const attendance = evObj.expected_attendance || asgn.ml_predicted_attendance || 0;
        const isConflict = conflictEventIds.has(asgn.event_id);
        const conflictClass = isConflict ? 'conflict-clash' : '';

        html += `
          <div class="week-event-card ${deptClass} ${conflictClass}" onclick="inspectEventFromDirectory(${asgn.event_id})" title="${asgn.event_name} (Click for AI explanation)">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="week-event-time">🕒 <strong>${info.startTimeStr}–${info.endTimeStr}</strong></span>
              <span class="card-priority-badge ${priority}">${priority.toUpperCase()}</span>
            </div>
            <strong class="week-event-title">${asgn.event_name}</strong>
            <div class="week-event-meta">📍 ${asgn.venue_name}</div>
            <div class="week-event-meta">👥 ${attendance} seats • ${asgn.department_name || 'Dept'}</div>
            ${isConflict ? '<div class="conflict-warning-tag" style="font-size:0.65rem;">⚠️ Conflict Overlap</div>' : ''}
          </div>
        `;
      });
    }

    html += `</div>`; // end week-day-events
    html += `</div>`; // end week-day-col
  });

  html += `</div>`; // end week-view-grid
  container.innerHTML = html;
}

// --------------------------------------------------------------------------
// 3. MONTH VIEW: Calendar with Event Badges (Clicking a date opens Day View)
// --------------------------------------------------------------------------
function renderMonthView(result) {
  const container = $('timetableGrid');
  const curDate = parseDateStr(timetableState.selectedDate);
  const year = curDate.getFullYear();
  const month = curDate.getMonth(); // 0-11

  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);
  const totalDays = lastDay.getDate();

  // Weekday of 1st day of month (Monday = 0, Tuesday = 1 ... Sunday = 6)
  const startDayIndex = (firstDay.getDay() + 6) % 7;

  // Group all assignments across all dates
  const assignmentsByDate = {};
  (result.assignments || []).forEach((a) => {
    const info = getAssignmentInterval(a);
    if (!info) return;
    if (!assignmentsByDate[info.dateStr]) assignmentsByDate[info.dateStr] = [];
    assignmentsByDate[info.dateStr].push(a);
  });

  const weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  let html = `<div class="month-view-grid">`;

  // Header row
  weekdays.forEach((w) => {
    html += `<div class="month-weekday-cell">${w}</div>`;
  });

  // Previous month trailing days
  const prevMonthLastDay = new Date(year, month, 0).getDate();
  for (let i = startDayIndex - 1; i >= 0; i--) {
    const prevDayNum = prevMonthLastDay - i;
    html += `<div class="month-day-cell other-month"><div class="month-day-header"><span class="month-day-num">${prevDayNum}</span></div></div>`;
  }

  // Days of current month
  for (let d = 1; d <= totalDays; d++) {
    const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const isSelected = dateStr === timetableState.selectedDate;
    const dayAssignments = assignmentsByDate[dateStr] || [];
    const filtered = filterAssignments(dayAssignments);
    const { conflictEventIds } = detectVenueConflicts(dayAssignments);

    html += `
      <div class="month-day-cell ${isSelected ? 'selected-day' : ''}" onclick="switchToDay('${dateStr}')" title="Click to open Day View for ${dateStr}">
        <div class="month-day-header">
          <span class="month-day-num">${d}</span>
          ${filtered.length > 0 ? `<span class="month-count-pill">${filtered.length} event${filtered.length === 1 ? '' : 's'}</span>` : ''}
        </div>
        <div class="month-pills-list">
    `;

    filtered.slice(0, 3).forEach((asgn) => {
      const info = getAssignmentInterval(asgn);
      let deptClass = 'dept-cse';
      if (asgn.department_name && asgn.department_name.includes('Elec')) deptClass = 'dept-ece';
      else if (asgn.department_name && asgn.department_name.includes('Bus')) deptClass = 'dept-mba';
      const isConflict = conflictEventIds.has(asgn.event_id);

      html += `
        <div class="month-event-pill ${deptClass} ${isConflict ? 'conflict-clash' : ''}" title="${asgn.event_name} (${info ? info.startTimeStr : ''})">
          ${info ? info.startTimeStr : ''} ${asgn.event_name}
        </div>
      `;
    });

    if (filtered.length > 3) {
      html += `<div style="font-size:0.65rem; color:var(--text-muted); font-weight:700;">+${filtered.length - 3} more</div>`;
    }

    html += `</div>`; // end month-pills-list
    html += `</div>`; // end month-day-cell
  }

  // Next month leading days to complete grid
  const currentTotalCells = startDayIndex + totalDays;
  const remainingCells = (7 - (currentTotalCells % 7)) % 7;
  for (let i = 1; i <= remainingCells; i++) {
    html += `<div class="month-day-cell other-month"><div class="month-day-header"><span class="month-day-num">${i}</span></div></div>`;
  }

  html += `</div>`; // end month-view-grid
  container.innerHTML = html;
}

// Wire up Timetable Toolbar and Filter Event Listeners
function initTimetableControls() {
  // 1. View Switcher Buttons
  const dayBtn = $('viewDayBtn');
  const weekBtn = $('viewWeekBtn');
  const monthBtn = $('viewMonthBtn');

  if (dayBtn) {
    dayBtn.addEventListener('click', () => {
      timetableState.viewMode = 'day';
      updateTimetableUIControls();
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }

  if (weekBtn) {
    weekBtn.addEventListener('click', () => {
      timetableState.viewMode = 'week';
      updateTimetableUIControls();
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }

  if (monthBtn) {
    monthBtn.addEventListener('click', () => {
      timetableState.viewMode = 'month';
      updateTimetableUIControls();
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }

  // 2. Date Navigation Arrows & Demo Button
  const prevBtn = $('prevDateBtn');
  const nextBtn = $('nextDateBtn');
  const todayBtn = $('todayDateBtn');
  const datePicker = $('timetableDatePicker');

  if (prevBtn) prevBtn.addEventListener('click', () => navigateDate(-1));
  if (nextBtn) nextBtn.addEventListener('click', () => navigateDate(1));
  if (todayBtn) {
    todayBtn.addEventListener('click', () => {
      timetableState.selectedDate = '2026-10-05';
      updateTimetableUIControls();
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }

  if (datePicker) {
    datePicker.addEventListener('change', (e) => {
      if (e.target.value) {
        timetableState.selectedDate = e.target.value;
        updateTimetableUIControls();
        if (currentSchedule) renderTimetable(currentSchedule);
      }
    });
  }

  // 3. Time Interval Selector
  const intervalSelect = $('intervalSelect');
  if (intervalSelect) {
    intervalSelect.addEventListener('change', (e) => {
      timetableState.intervalMinutes = parseInt(e.target.value, 10) || 60;
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }

  // 3b. Conflict Heatmap Toggle
  const heatmapToggle = $('heatmapToggle');
  if (heatmapToggle) {
    heatmapToggle.addEventListener('change', () => {
      if (currentSchedule && timetableState.viewMode === 'day') {
        renderDayView(currentSchedule);
      }
    });
  }

  // 3c. Fit-to-Screen Toggle
  const fitScreenToggle = $('fitScreenToggle');
  if (fitScreenToggle) {
    fitScreenToggle.checked = Boolean(timetableState.fitScreen);
    fitScreenToggle.addEventListener('change', (e) => {
      timetableState.fitScreen = e.target.checked;
      if (currentSchedule && timetableState.viewMode === 'day') {
        renderDayView(currentSchedule);
      }
    });
  }

  // 4. Filters & Search
  const filterDept = $('filterDeptSelect');
  const filterVenue = $('filterVenueSelect');
  const filterPriority = $('filterPrioritySelect');
  const filterSearch = $('filterSearchInput');
  const clearFiltersBtn = $('clearFiltersBtn');

  // Guard against browser password/credential autofill injecting into search input:
  // Reset input values on load
  if (filterSearch) {
    filterSearch.value = '';
    filterSearch.setAttribute('autocomplete', 'off');
    filterSearch.setAttribute('autocorrect', 'off');
    filterSearch.setAttribute('autocapitalize', 'off');
    filterSearch.setAttribute('spellcheck', 'false');
  }
  timetableState.filters = { deptId: 'all', venueId: 'all', priority: 'all', search: '' };

  if (filterDept) {
    filterDept.addEventListener('change', (e) => {
      timetableState.filters.deptId = e.target.value;
      updateTimetableUIControls();
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }

  if (filterVenue) {
    filterVenue.addEventListener('change', (e) => {
      timetableState.filters.venueId = e.target.value;
      updateTimetableUIControls();
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }

  if (filterPriority) {
    filterPriority.addEventListener('change', (e) => {
      timetableState.filters.priority = e.target.value;
      updateTimetableUIControls();
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }

  if (filterSearch) {
    const handleSearchInput = (e) => {
      const val = e.target.value.trim();
      // Block accidental password manager autofill injecting login emails into event search
      if (val === 'admin@lpu.in' || (val.includes('@') && !val.includes(' '))) {
        const hasMatchingEvent = (state && state.events ? state.events.some(ev => ev.name.toLowerCase().includes(val.toLowerCase())) : false);
        if (!hasMatchingEvent) {
          e.target.value = '';
          timetableState.filters.search = '';
          updateTimetableUIControls();
          if (currentSchedule) renderTimetable(currentSchedule);
          return;
        }
      }
      timetableState.filters.search = val;
      updateTimetableUIControls();
      if (currentSchedule) renderTimetable(currentSchedule);
    };

    filterSearch.addEventListener('input', handleSearchInput);
    filterSearch.addEventListener('search', handleSearchInput);
  }

  if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener('click', () => {
      timetableState.filters = { deptId: 'all', venueId: 'all', priority: 'all', search: '' };
      if (filterDept) filterDept.value = 'all';
      if (filterVenue) filterVenue.value = 'all';
      if (filterPriority) filterPriority.value = 'all';
      if (filterSearch) filterSearch.value = '';
      updateTimetableUIControls();
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }
}

// --------------------------------------------------------------------------
// Explainable AI Panel
// --------------------------------------------------------------------------
async function inspectEvent(eventId) {
  try {
    const data = await api(`/api/explain/${eventId}`);
    const container = $('explainReport');

    const statusBadge = data.status === 'Scheduled'
      ? '<span class="badge" style="background:#dcfce7; color:#15803d;">SCHEDULED</span>'
      : '<span class="badge" style="background:#fee2e2; color:#b91c1c;">UNRESOLVED CLASH</span>';

    container.innerHTML = `
      <div class="explain-card" style="grid-column: 1 / -1; background: var(--panel-bg); border-left: 4px solid ${data.status === 'Scheduled' ? '#22c55e' : '#ef4444'};">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <h3>${data.event_name} ${statusBadge}</h3>
            <p class="muted">Department: ${data.department_name} • Priority: <span class="badge ${data.priority}">${data.priority}</span></p>
          </div>
          ${data.venue_assigned ? `<div style="text-align:right;"><strong>${data.venue_assigned}</strong><br><small class="muted">${data.time_assigned.map(fmtFull).join('<br>')}</small></div>` : ''}
        </div>
      </div>

      <div class="explain-card">
        <h4>🔒 1. Hard Constraints Satisfied</h4>
        <ul>
          ${data.hard_constraints_satisfied.map((x) => `<li>${x}</li>`).join('')}
        </ul>
      </div>

      <div class="explain-card">
        <h4>✨ 2. Preferences Satisfied vs Sacrificed</h4>
        <p style="font-size:0.75rem; color:#15803d; font-weight:700; margin-bottom:4px;">SATISFIED:</p>
        <ul>
          ${data.soft_preferences_satisfied.length ? data.soft_preferences_satisfied.map((x) => `<li>${x}</li>`).join('') : '<li>None</li>'}
        </ul>
        <p style="font-size:0.75rem; color:#b45309; font-weight:700; margin-top:8px; margin-bottom:4px;">SACRIFICED (Soft Costs):</p>
        <ul>
          ${data.soft_preferences_sacrificed.length ? data.soft_preferences_sacrificed.map((x) => `<li>${x}</li>`).join('') : '<li>No penalties incurred.</li>'}
        </ul>
      </div>

      <div class="explain-card">
        <h4>⚖️ 3. Conflict Attribution & Winner</h4>
        <ul>
          ${data.conflict_details.map((x) => `<li>${x}</li>`).join('')}
        </ul>
      </div>

      <div class="explain-card">
        <h4>💡 4. Nearest Alternative Recommendation</h4>
        ${data.alternative_options
          ? `<p style="font-size:0.85rem; color:#0369a1; line-height:1.4;">${data.alternative_options.reason}</p>`
          : '<p style="font-size:0.85rem; color:var(--text-muted);">Event successfully placed; no relocation required.</p>'
        }
      </div>
    `;

    if ($('explainEventSelect')) $('explainEventSelect').value = eventId;
  } catch (err) {
    console.error(err);
  }
}

function inspectEventFromDirectory(eventId) {
  // Switch to Explainable AI tab using the unified controller
  setActiveTab('explainable');
  inspectEvent(eventId);
}

// --------------------------------------------------------------------------
// What-If Scenario Simulation
// --------------------------------------------------------------------------
let scenarioHistoryList = [];

function setWhatIfStatus(statusText, badgeStyle) {
  const badge = $('whatIfStatusBadge');
  if (badge) {
    badge.textContent = statusText;
    if (badgeStyle === 'running') {
      badge.style.background = 'rgba(234, 179, 8, 0.2)';
      badge.style.color = '#fde047';
      badge.style.borderColor = 'rgba(234, 179, 8, 0.4)';
    } else if (badgeStyle === 'complete') {
      badge.style.background = 'rgba(34, 197, 94, 0.2)';
      badge.style.color = '#4ade80';
      badge.style.borderColor = 'rgba(34, 197, 94, 0.4)';
    } else if (badgeStyle === 'error') {
      badge.style.background = 'rgba(239, 68, 68, 0.2)';
      badge.style.color = '#f87171';
      badge.style.borderColor = 'rgba(239, 68, 68, 0.4)';
    } else {
      badge.style.background = 'rgba(59, 130, 246, 0.2)';
      badge.style.color = '#60a5fa';
      badge.style.borderColor = 'rgba(59, 130, 246, 0.4)';
    }
  }
}

function syncWhatIfEventSelection() {
  if (!state || !state.events) return;
  const evId = parseInt($('whatIfEventSelect').value, 10);
  const ev = state.events.find((e) => e.id === evId);
  if (ev) {
    if ($('whatIfPrioritySelect')) $('whatIfPrioritySelect').value = ev.priority || 'normal';
    if ($('whatIfAttendanceInput')) $('whatIfAttendanceInput').value = ev.expected_attendance || 50;
    if ($('whatIfOrigAttendance')) $('whatIfOrigAttendance').textContent = `(Original: ${ev.expected_attendance || 50})`;
  }
}

function syncWhatIfVenueSelection() {
  if (!state || !state.venues) return;
  const vId = parseInt($('whatIfVenueSelect').value, 10);
  const v = state.venues.find((v) => v.id === vId);
  if (v) {
    if ($('whatIfVenueCapacity')) $('whatIfVenueCapacity').value = v.capacity || 50;
    if ($('whatIfOrigCapacity')) $('whatIfOrigCapacity').textContent = `(Original: ${v.capacity || 50})`;
  }
}

function syncWhatIfTimeShiftSelection() {
  if (!state || !state.events) return;
  const evIdRaw = $('whatIfTimeShiftEventSelect') ? $('whatIfTimeShiftEventSelect').value : '';
  const origTimeSpan = $('whatIfOrigStartTime');
  if (!evIdRaw) {
    if (origTimeSpan) origTimeSpan.textContent = 'Select an event';
    return;
  }
  const evId = parseInt(evIdRaw, 10);
  const ev = state.events.find((e) => e.id === evId);
  if (!ev) {
    if (origTimeSpan) origTimeSpan.textContent = 'Select an event';
    return;
  }

  let timeStr = 'Not Scheduled';
  if (currentSchedule && currentSchedule.assignments) {
    const asgn = currentSchedule.assignments.find((a) => a.event_id === evId);
    if (asgn && asgn.slots && asgn.slots.length > 0) {
      try {
        const dt = new Date(asgn.slots[0]);
        const startH = String(dt.getHours()).padStart(2, '0');
        const startM = String(dt.getMinutes()).padStart(2, '0');
        const endDt = new Date(new Date(asgn.slots[asgn.slots.length - 1]).getTime() + 3600000);
        const endH = String(endDt.getHours()).padStart(2, '0');
        const endM = String(endDt.getMinutes()).padStart(2, '0');
        timeStr = `${startH}:${startM}–${endH}:${endM}`;
      } catch (err) {
        timeStr = asgn.slots[0];
      }
    }
  }
  if (origTimeSpan) origTimeSpan.textContent = `${ev.name} (${timeStr})`;
}

function updateUnavailabilityReasonState() {
  // Unavailability Reason dropdown is always interactive
}

function toggleAdvancedControls() {
  const body = $('advancedControlsBody');
  const chevron = $('advancedChevron');
  if (body) {
    const isHidden = body.classList.contains('hidden');
    if (isHidden) {
      body.classList.remove('hidden');
      if (chevron) chevron.textContent = '▲';
    } else {
      body.classList.add('hidden');
      if (chevron) chevron.textContent = '▼';
    }
  }
}

function resetWhatIfSimulation() {
  syncWhatIfEventSelection();
  syncWhatIfVenueSelection();
  syncWhatIfTimeShiftSelection();
  if ($('whatIfTimeShiftEventSelect')) $('whatIfTimeShiftEventSelect').value = '';
  if ($('whatIfSimulatedStartHourSelect')) $('whatIfSimulatedStartHourSelect').value = '';
  if ($('whatIfOrigStartTime')) $('whatIfOrigStartTime').textContent = 'Select an event';
  if ($('whatIfUnavailableVenueSelect')) $('whatIfUnavailableVenueSelect').value = '';
  if ($('whatIfUnavailabilityReasonSelect')) {
    $('whatIfUnavailabilityReasonSelect').value = 'Maintenance';
    $('whatIfUnavailabilityReasonSelect').disabled = false;
  }
  if ($('whatIfPreserveTimeCheck')) $('whatIfPreserveTimeCheck').checked = false;
  if ($('whatIfPreserveVenueCheck')) $('whatIfPreserveVenueCheck').checked = false;
  if ($('whatIfAllowTimeShiftCheck')) $('whatIfAllowTimeShiftCheck').checked = true;
  if ($('whatIfAllowVenueShiftCheck')) $('whatIfAllowVenueShiftCheck').checked = true;
  if ($('whatIfAllowCascadeCheck')) $('whatIfAllowCascadeCheck').checked = true;
  if ($('whatIfMaxSoftCostInput')) $('whatIfMaxSoftCostInput').value = '1000';
  if ($('whatIfMaxCascadeDepthInput')) $('whatIfMaxCascadeDepthInput').value = '3';
  if ($('whatIfPriorityHandlingSelect')) $('whatIfPriorityHandlingSelect').value = 'strict';
  if ($('whatIfConflictResolutionSelect')) $('whatIfConflictResolutionSelect').value = 'auto';

  const impactSummary = $('whatIfImpactSummary');
  const dependencyChain = $('whatIfDependencyChain');
  const diffEmpty = $('whatifDiffEmpty');
  const table = $('whatIfDiffTable');
  const tbody = $('whatIfDiffBody');
  const loading = $('simulationLoading');
  const errCard = $('simulationErrorCard');

  if (impactSummary) impactSummary.classList.add('hidden');
  if (dependencyChain) dependencyChain.classList.add('hidden');
  if (table) table.classList.add('hidden');
  if (loading) loading.classList.add('hidden');
  if (errCard) errCard.classList.add('hidden');
  if (diffEmpty) diffEmpty.classList.remove('hidden');
  if (tbody) tbody.innerHTML = '';
  setWhatIfStatus('Ready', 'ready');
}

function renderScenarioHistory() {
  const table = $('scenarioHistoryTable');
  const emptyBox = $('scenarioHistoryEmpty');
  const tbody = $('scenarioHistoryBody');

  if (!scenarioHistoryList || scenarioHistoryList.length === 0) {
    if (table) table.classList.add('hidden');
    if (emptyBox) emptyBox.classList.remove('hidden');
    if (tbody) tbody.innerHTML = '';
    return;
  }

  if (emptyBox) emptyBox.classList.add('hidden');
  if (table) table.classList.remove('hidden');

  if (tbody) {
    tbody.innerHTML = scenarioHistoryList
      .map((item) => `
        <tr>
          <td><strong style="color:#60a5fa;">${item.id}</strong></td>
          <td>${item.target_event}</td>
          <td>${item.param}</td>
          <td>${item.before_state}</td>
          <td><strong>${item.after_state}</strong></td>
          <td><span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3);">${item.change_type}</span></td>
          <td>${item.cost >= 0 ? '+' + item.cost : item.cost}</td>
          <td><strong>${item.changes}</strong></td>
        </tr>
      `)
      .join('');
  }
}

async function runWhatIfSimulation() {
  const targetEventId = parseInt($('whatIfEventSelect').value, 10);
  const targetPriority = $('whatIfPrioritySelect').value;
  const targetAttendance = parseInt($('whatIfAttendanceInput').value, 10);

  const targetVenueId = parseInt($('whatIfVenueSelect').value, 10);
  const targetCapacity = parseInt($('whatIfVenueCapacity').value, 10);

  const unavailVenueIdRaw = $('whatIfUnavailableVenueSelect') ? $('whatIfUnavailableVenueSelect').value : '';
  const unavailableVenueId = unavailVenueIdRaw ? parseInt(unavailVenueIdRaw, 10) : null;
  const unavailabilityReason = $('whatIfUnavailabilityReasonSelect') ? $('whatIfUnavailabilityReasonSelect').value : 'Maintenance';

  const timeShiftEventIdRaw = $('whatIfTimeShiftEventSelect') ? $('whatIfTimeShiftEventSelect').value : '';
  const timeShiftEventId = timeShiftEventIdRaw ? parseInt(timeShiftEventIdRaw, 10) : null;
  const simulatedStartHourRaw = $('whatIfSimulatedStartHourSelect') ? $('whatIfSimulatedStartHourSelect').value : '';
  const simulatedStartHour = simulatedStartHourRaw !== '' ? parseInt(simulatedStartHourRaw, 10) : null;

  // Inline Validation
  if (isNaN(targetAttendance) || targetAttendance <= 0) {
    alert('Simulated Expected Attendance must be a positive integer greater than 0.');
    return;
  }
  if (isNaN(targetCapacity) || targetCapacity <= 0) {
    alert('Simulated Venue Capacity must be a positive integer greater than 0.');
    return;
  }
  if (timeShiftEventId !== null && (simulatedStartHour === null || isNaN(simulatedStartHour))) {
    alert('Please select a valid Simulated Start Hour (0–23) for the time shift override.');
    return;
  }

  const payload = {
    event_overrides: (() => {
      // Only include event_overrides if priority or attendance actually differ from baseline
      const baseEvent = (state.events || []).find((e) => e.id === targetEventId);
      const changes = {};
      if (!baseEvent || targetPriority !== baseEvent.priority) changes.priority = targetPriority;
      if (!baseEvent || targetAttendance !== baseEvent.expected_attendance) changes.expected_attendance = targetAttendance;
      return Object.keys(changes).length > 0 ? { [targetEventId]: changes } : {};
    })(),
    venue_overrides: (() => {
      // Only include venue_overrides if capacity actually differs from baseline
      const baseVenue = (state.venues || []).find((v) => v.id === targetVenueId);
      if (!baseVenue || targetCapacity !== baseVenue.capacity) {
        return { [targetVenueId]: { capacity: targetCapacity } };
      }
      return {};
    })(),
    unavailable_venue_id: unavailableVenueId,
    unavailability_reason: unavailabilityReason,
    target_time_shift_event_id: timeShiftEventId,
    simulated_start_hour: simulatedStartHour,
    preserve_original_time: $('whatIfPreserveTimeCheck') ? $('whatIfPreserveTimeCheck').checked : false,
    preserve_original_venue: $('whatIfPreserveVenueCheck') ? $('whatIfPreserveVenueCheck').checked : false,
    allow_time_shift: $('whatIfAllowTimeShiftCheck') ? $('whatIfAllowTimeShiftCheck').checked : true,
    allow_venue_shift: $('whatIfAllowVenueShiftCheck') ? $('whatIfAllowVenueShiftCheck').checked : true,
    allow_cascade: $('whatIfAllowCascadeCheck') ? $('whatIfAllowCascadeCheck').checked : true,
    max_allowed_soft_cost: parseInt($('whatIfMaxSoftCostInput') ? $('whatIfMaxSoftCostInput').value : 1000, 10),
    max_cascade_depth: parseInt($('whatIfMaxCascadeDepthInput') ? $('whatIfMaxCascadeDepthInput').value : 3, 10),
    priority_handling: $('whatIfPriorityHandlingSelect') ? $('whatIfPriorityHandlingSelect').value : 'strict',
    conflict_resolution: $('whatIfConflictResolutionSelect') ? $('whatIfConflictResolutionSelect').value : 'auto',
  };

  const btn = $('runWhatIfBtn');
  const loading = $('simulationLoading');
  const errCard = $('simulationErrorCard');
  btn.disabled = true;
  btn.textContent = 'Simulating...';
  if (loading) loading.classList.remove('hidden');
  if (errCard) errCard.classList.add('hidden');
  setWhatIfStatus('Simulation Running', 'running');

  try {
    const result = await api('/api/whatif', {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    setWhatIfStatus('Simulation Complete', 'complete');

    // 1. Render Impact Summary Cards
    const impactSummary = $('whatIfImpactSummary');
    if (impactSummary) {
      impactSummary.classList.remove('hidden');
      $('impactSchedVal').textContent = result.schedule_impact;
      $('impactVenueVal').textContent = result.venue_impact;
      $('impactTimeVal').textContent = result.time_impact;
      $('impactCostVal').textContent = result.cost_impact;
      $('impactChangesVal').textContent = result.total_changes;
    }

    // 1b. Render Granular Impact Cards
    const granularSummary = $('whatIfGranularSummary');
    if (granularSummary) {
      granularSummary.classList.remove('hidden');
      $('impactChangedEventsVal').textContent = result.changed_events ?? result.total_changes ?? 0;
      $('impactTimeShiftsVal').textContent = result.time_shifts ?? 0;
      $('impactVenueShiftsVal').textContent = result.venue_shifts ?? 0;
      $('impactUnresolvedVal').textContent = result.unresolved_events ?? 0;

      const violEl = $('impactViolationsVal');
      const vCount = result.hard_constraint_violations ?? 0;
      violEl.textContent = vCount;
      violEl.style.color = vCount > 0 ? '#f87171' : '#4ade80';

      const feasEl = $('impactFeasibilityVal');
      const feas = result.feasibility_status || '—';
      feasEl.textContent = feas;
      if (feas.includes('INFEASIBLE')) {
        feasEl.style.color = '#f87171';
      } else if (feas.includes('PARTIALLY')) {
        feasEl.style.color = '#fbbf24';
      } else {
        feasEl.style.color = '#4ade80';
      }
    }

    // 2. Render Dependency Chain
    const depChainWrap = $('whatIfDependencyChain');
    const depChainList = $('dependencyChainList');
    if (depChainWrap && depChainList) {
      if (result.dependency_chain && result.dependency_chain.length > 0) {
        depChainWrap.classList.remove('hidden');
        depChainList.innerHTML = result.dependency_chain
          .map((step) => {
            let style = 'background:rgba(30,58,138,0.4); color:#93c5fd; border:1px solid rgba(59,130,246,0.4);';
            if (step === 'FINAL FEASIBLE SCHEDULE') {
              style = 'background:rgba(34,197,94,0.2); color:#4ade80; border:1px solid rgba(34,197,94,0.4); font-weight:bold;';
            } else if (step === 'PARTIALLY FEASIBLE SCHEDULE') {
              style = 'background:rgba(245,158,11,0.2); color:#fbbf24; border:1px solid rgba(245,158,11,0.4); font-weight:bold;';
            } else if (step === 'INFEASIBLE SCHEDULE') {
              style = 'background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-weight:bold;';
            }
            return `<span class="badge" style="${style} font-size:0.75rem;">${step}</span>`;
          })
          .join(' <span style="color:#64748b; font-weight:bold;">➔</span> ');
      } else {
        depChainWrap.classList.add('hidden');
      }
    }

    // 3. Render Diff Table
    $('whatifDiffEmpty').classList.add('hidden');
    const table = $('whatIfDiffTable');
    table.classList.remove('hidden');

    function formatSlotRange(slots) {
      if (!slots || slots.length === 0) return '';
      try {
        const startDt = new Date(slots[0]);
        const endDt = new Date(slots[slots.length - 1]);
        endDt.setHours(endDt.getHours() + 1);
        const startStr = startDt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        const endStr = endDt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        return `${startStr}–${endStr}`;
      } catch (e) {
        return slots.join(', ');
      }
    }

    const tbody = $('whatIfDiffBody');
    tbody.innerHTML = result.differences
      .map((d) => {
        const bVenue = d.before_venue || '';
        const aVenue = d.after_venue || '';
        const bTime = formatSlotRange(d.before_slots);
        const aTime = formatSlotRange(d.after_slots);

        const venueChanged = bVenue && aVenue && (bVenue !== aVenue);
        const timeChanged = bTime && aTime && (bTime !== aTime);

        let changeBadge = '<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3);">UNCHANGED</span>';
        const ct = (d.change_type || '').toUpperCase();

        if (d.before_status === 'Scheduled' && d.after_status === 'Scheduled') {
          if (!venueChanged && !timeChanged) {
            changeBadge = '<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3);">UNCHANGED</span>';
          } else if (venueChanged && !timeChanged) {
            changeBadge = '<span class="badge" style="background:rgba(249,115,22,0.15); color:#f97316; border:1px solid rgba(249,115,22,0.3);">VENUE SHIFT</span>';
          } else if (!venueChanged && timeChanged) {
            changeBadge = '<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3);">TIME SHIFT</span>';
          } else if (venueChanged && timeChanged) {
            changeBadge = '<span class="badge" style="background:rgba(249,115,22,0.15); color:#f97316; border:1px solid rgba(249,115,22,0.3);">VENUE SHIFT</span> <span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3);">TIME SHIFT</span>';
          }
        } else if (ct === 'CAPACITY CONFLICT') {
          changeBadge = '<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.3);">CAPACITY CONFLICT</span>';
        } else if (ct === 'REMAINS UNSOLVED' || ct === 'REMAINS_UNSOLVED') {
          changeBadge = '<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.3);">REMAINS UNSOLVED</span>';
        } else if (ct === 'UNRESOLVED') {
          changeBadge = '<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.3);">UNRESOLVED</span>';
        } else if (ct === 'TIME SHIFT CONFLICT' || ct === 'TIME_SHIFT_CONFLICT') {
          changeBadge = '<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.3);">UNRESOLVED (TIME SHIFT CONFLICT)</span>';
        } else if (ct === 'UNSOLVED') {
          changeBadge = '<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.3);">UNSOLVED</span>';
        } else if (ct === 'FEASIBLE' || ct === 'RESOLVED') {
          changeBadge = '<span class="badge" style="background:rgba(34,197,94,0.15); color:#4ade80; border:1px solid rgba(34,197,94,0.3);">FEASIBLE</span>';
        }

        let beforeCell = d.before_status;
        if (d.before_venue) {
          const tStr = formatSlotRange(d.before_slots);
          beforeCell = `${d.before_venue}${tStr ? ` • ${tStr}` : ''}`;
        }

        let afterCell = d.after_status;
        if (d.after_venue) {
          const tStr = formatSlotRange(d.after_slots);
          afterCell = `${d.after_venue}${tStr ? ` • ${tStr}` : ''}`;
        }

        const costDisplay = d.cost_change > 0 ? `+${d.cost_change}` : (d.cost_change < 0 ? `${d.cost_change}` : '0');

        return `
        <tr>
          <td><strong>${d.event_name}</strong></td>
          <td>${changeBadge}</td>
          <td>${beforeCell}</td>
          <td><strong>${afterCell}</strong></td>
          <td>${d.details}</td>
          <td><code>${costDisplay}</code></td>
        </tr>
      `;
      })
      .join('');

    // 4. Add to Scenario History
    let targetEvObj = null;
    let targetEvName = 'System Simulation';
    let changedParamStr = `Attendance: ${targetAttendance}`;

    if (timeShiftEventId && state.events) {
      targetEvObj = state.events.find(e => e.id === timeShiftEventId);
      if (targetEvObj) {
        targetEvName = targetEvObj.name;
        changedParamStr = `Simulated Start Hour: ${simulatedStartHour}:00`;
      }
    } else if (targetEventId && state.events) {
      targetEvObj = state.events.find(e => e.id === targetEventId);
      if (targetEvObj) targetEvName = targetEvObj.name;
    }

    if (unavailableVenueId) {
      changedParamStr = `Venue Unavailable: ${unavailabilityReason}`;
    } else if (targetCapacity && state.venues) {
      const vObj = state.venues.find(v => v.id === targetVenueId);
      if (vObj && vObj.capacity !== targetCapacity) {
        changedParamStr = `Capacity: ${vObj.capacity} → ${targetCapacity}`;
      }
    }

    const histTargetId = timeShiftEventId || targetEventId;
    const mainDiff = result.differences.find(d => d.event_id === histTargetId) || result.differences[0];

    const historyItem = {
      id: result.scenario_id || `SCEN-${scenarioHistoryList.length + 1}`,
      target_event: targetEvName,
      param: changedParamStr,
      before_state: mainDiff ? `${mainDiff.before_status} (${mainDiff.before_venue || 'N/A'})` : 'Baseline',
      after_state: mainDiff ? `${mainDiff.after_status} (${mainDiff.after_venue || 'N/A'})` : 'Simulated',
      change_type: mainDiff ? mainDiff.change_type : 'UNCHANGED',
      cost: result.additional_soft_cost,
      changes: result.total_changes,
    };

    scenarioHistoryList.unshift(historyItem);
    renderScenarioHistory();

  } catch (e) {
    setWhatIfStatus('Simulation Error', 'error');
    if (errCard) {
      errCard.classList.remove('hidden');
      const errMsgEl = $('simulationErrorMsg');
      if (errMsgEl) errMsgEl.textContent = e.message || 'An unexpected error occurred during simulation.';
    }
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>🚀</span> Run Simulation';
    if (loading) loading.classList.add('hidden');
  }
}

// --------------------------------------------------------------------------
// Analytics & Solver Benchmarking
// --------------------------------------------------------------------------
async function loadAnalytics() {
  try {
    const data = await api('/api/analytics');

    // 1. Venue utilization progress bars
    const utilContainer = $('venueUtilList');
    utilContainer.innerHTML = (data.venue_utilization || [])
      .map((v) => `
        <div class="util-item">
          <div class="util-meta">
            <span>${v.venue_name} (${v.capacity} cap)</span>
            <span>${v.booked_slots}/${v.total_slots_available} slots (${v.utilization_rate_pct}%)</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" style="width: ${Math.min(100, v.utilization_rate_pct)}%"></div>
          </div>
        </div>
      `)
      .join('');

    // 2. Department Equity
    const deptContainer = $('deptEquityList');
    deptContainer.innerHTML = (data.department_equity || [])
      .map((d) => `
        <div class="util-item">
          <div class="util-meta">
            <span>${d.department_name}</span>
            <span>${d.events_scheduled}/${d.events_requested} events (${d.satisfaction_rate_pct}%)</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" style="width: ${Math.min(100, d.satisfaction_rate_pct)}%; background:#3b82f6;"></div>
          </div>
        </div>
      `)
      .join('');
  } catch (err) {
    console.error('Analytics load error:', err);
  }
}

async function runOrToolsBenchmark() {
  const infoEl = $('solverBenchmarkInfo');
  infoEl.innerHTML = '<p class="muted">Running custom CSP solver and Google OR-Tools CP-SAT in parallel...</p>';

  try {
    const res = await api('/api/schedule/generate', {
      method: 'POST',
      body: JSON.stringify({ max_nodes: 100000, use_ortools: true }),
    });

    const cmp = res.comparison;
    if (!cmp) {
      infoEl.innerHTML = '<p class="muted">OR-Tools benchmark finished. Check latest schedule.</p>';
      return;
    }

    infoEl.innerHTML = `
      <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-top:8px;">
        <div style="background:#f1f5f9; padding:10px; border-radius:8px;">
          <strong style="display:block; font-size:0.75rem; color:#64748b;">CUSTOM CSP SOLVER</strong>
          <span style="font-size:1.1rem; font-weight:800;">${cmp.csp_time_ms} ms</span>
          <div style="font-size:0.75rem; color:#64748b;">${cmp.csp_nodes} explored nodes</div>
        </div>
        <div style="background:#eff6ff; padding:10px; border-radius:8px;">
          <strong style="display:block; font-size:0.75rem; color:#2563eb;">GOOGLE OR-TOOLS CP-SAT</strong>
          <span style="font-size:1.1rem; font-weight:800;">${cmp.ortools_time_ms} ms</span>
          <div style="font-size:0.75rem; color:#2563eb;">Status: ${cmp.ortools_status}</div>
        </div>
        <div style="background:#dcfce7; padding:10px; border-radius:8px;">
          <strong style="display:block; font-size:0.75rem; color:#15803d;">SCHEDULE CONCORDANCE</strong>
          <span style="font-size:1.1rem; font-weight:800;">${cmp.match_percentage}%</span>
          <div style="font-size:0.75rem; color:#15803d;">Full Agreement</div>
        </div>
      </div>
    `;
    renderTimetable(res);
  } catch (e) {
    infoEl.innerHTML = `<p style="color:#dc2626;">Error: ${e.message}</p>`;
  }
}

// --------------------------------------------------------------------------
// Constraint Pill Educational Modals
// --------------------------------------------------------------------------
const pillExplanations = {
  capacity: {
    title: 'Hard Constraint: Capacity and Venue Type',
    body: `
      <p><b>Constraint Formulation:</b></p>
      <code>Venue.capacity &ge; Event.expected_attendance &and; (Event.required_venue_type &isin; {null, Venue.venue_type})</code>
      <p style="margin-top:10px;">The CSP solver verifies that the chosen venue has adequate physical seating capacity. In the demo, the <b>500-Seat Mega Orientation</b> cannot be placed because the largest hall on campus is Central Auditorium (capacity 300).</p>
    `,
  },
  availability: {
    title: 'Hard Constraint: Venue and Staff Availability',
    body: `
      <p><b>Constraint Formulation:</b></p>
      <code>Event.slots &sube; (Venue.available_slots &cap; &Intersection; Staff.available_slots)</code>
      <p style="margin-top:10px;">Events may only be scheduled during official building operational hours when all assigned faculty members are certified available.</p>
    `,
  },
  'no-overlap': {
    title: 'Hard Constraint: No Overlapping Resource Use',
    body: `
      <p><b>Constraint Formulation:</b></p>
      <code>&forall; E1, E2: (E1.venue == E2.venue &or; (E1.staff &cap; E2.staff &ne; &empty;)) &rArr; (E1.slots &cap; E2.slots = &empty;)</code>
      <p style="margin-top:10px;">Prevents double-booking. When <b>AI/ML Research Symposium</b> (Critical) and <b>Startup Pitch Day</b> (High) compete for Mr. Bob at 10:00 AM, the solver's priority rules ensure the Critical event prevails.</p>
    `,
  },
  'equipment-limits': {
    title: 'Hard Constraint: Equipment Quantity Limits',
    body: `
      <p><b>Constraint Formulation:</b></p>
      <code>&forall; slot t, equipment eq: &sum; Usage(E, eq, t) &le; Stock(eq)</code>
      <p style="margin-top:10px;">Pooled university hardware (e.g. only 1 Portable PA System, 1 VR Kit) is monitored continuously at every hourly interval.</p>
    `,
  },
  'allowed-times': {
    title: 'Hard Constraint: Allowed & Fixed Event Times',
    body: `
      <p><b>Constraint Formulation:</b></p>
      <code>(Event.fixed_start_slot &rArr; start == fixed_slot) &and; (Consecutive Gap &ge; buffer_slots)</code>
      <p style="margin-top:10px;">Enforces mandatory start constraints and maintains buffer setup times between back-to-back presentations.</p>
    `,
  },
  'preferred-venue': {
    title: 'Soft Preference: Preferred Venue',
    body: `
      <p><b>Objective Function Penalty:</b> +3 soft cost if <code>Venue &ne; PreferredVenue</code>.</p>
      <p style="margin-top:10px;">Encourages events to use their department's home hall while allowing graceful reassignment to prevent scheduling deadlocks.</p>
    `,
  },
  'preferred-hour': {
    title: 'Soft Preference: Preferred Start Hour',
    body: `
      <p><b>Objective Function Penalty:</b> +|AssignedHour - PreferredHour|.</p>
      <p style="margin-top:10px;">Minimizes temporal displacement from organizers' ideal schedules.</p>
    `,
  },
  'avoid-late': {
    title: 'Soft Preference: Avoid Late Sessions',
    body: `
      <p><b>Objective Function Penalty:</b> +2 soft cost if conclusion extends past 17:00.</p>
      <p style="margin-top:10px;">Prioritizes prime academic daylight hours for student commuter convenience.</p>
    `,
  },
  'priority-order': {
    title: 'Soft Preference & Heuristic: Priority-Aware Search Order',
    body: `
      <p><b>Variable Ordering:</b> Critical (0) &gt; High (1) &gt; Normal (2) &gt; Low (3).</p>
      <p style="margin-top:10px;">Coupled with <b>Minimum Remaining Values (MRV)</b> and the <b>Degree Heuristic</b>, higher-tier events claim prime venues before general lectures compete.</p>
    `,
  },
};

function initPillInteractions() {
  document.querySelectorAll('.constraint-pill').forEach((pill) => {
    pill.addEventListener('click', () => {
      const key = pill.dataset.constraint || pill.dataset.preference;
      const data = pillExplanations[key];
      if (!data) return;

      $('pillDetailTitle').textContent = data.title;
      $('pillDetailBody').innerHTML = data.body;
      $('pillDetailModal').classList.remove('hidden');
    });
  });

  $('closePillModalBtn').addEventListener('click', () => $('pillDetailModal').classList.add('hidden'));
  $('okPillModalBtn').addEventListener('click', () => $('pillDetailModal').classList.add('hidden'));
}

// --------------------------------------------------------------------------
// Event CRUD Modal
// --------------------------------------------------------------------------
function initEventModal() {
  const modal = $('eventModal');
  const addBtn = $('addEventBtn');
  const closeBtn = $('closeModalBtn');
  const cancelBtn = $('cancelModalBtn');
  const form = $('eventForm');
  const errorBanner = $('eventFormError');

  function showError(msg) {
    if (errorBanner) {
      errorBanner.textContent = msg;
      errorBanner.classList.remove('hidden');
    } else {
      alert(msg);
    }
  }

  function clearError() {
    if (errorBanner) {
      errorBanner.textContent = '';
      errorBanner.classList.add('hidden');
    }
  }

  if (addBtn) {
    addBtn.addEventListener('click', () => {
      clearError();
      modal.classList.remove('hidden');
    });
  }
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      clearError();
      modal.classList.add('hidden');
    });
  }
  if (cancelBtn) {
    cancelBtn.addEventListener('click', () => {
      clearError();
      modal.classList.add('hidden');
    });
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearError();

    const title = $('eventTitleInput').value.trim();
    const deptId = parseInt($('eventDeptSelect').value, 10);
    const priority = $('eventPrioritySelect').value;
    const duration = parseInt($('eventDurationInput').value, 10);
    const attendance = parseInt($('eventAttendanceInput').value, 10);
    const venueType = $('eventVenueTypeSelect').value || null;
    const prefHourRaw = $('eventPreferredHourInput').value;

    if (!title) {
      showError('Event Title is required and cannot be empty.');
      return;
    }
    if (isNaN(deptId)) {
      showError('Please select a valid Department.');
      return;
    }
    if (!priority) {
      showError('Please select a Priority.');
      return;
    }
    if (isNaN(duration) || duration <= 0) {
      showError('Duration must be a positive number greater than 0.');
      return;
    }
    if (isNaN(attendance) || attendance <= 0) {
      showError('Expected Attendance must be a positive integer greater than 0.');
      return;
    }

    let prefHour = null;
    if (prefHourRaw !== '' && prefHourRaw !== null) {
      prefHour = parseInt(prefHourRaw, 10);
      if (isNaN(prefHour) || prefHour < 0 || prefHour > 23) {
        showError('Preferred Start Hour must be an integer between 0 and 23.');
        return;
      }
    }

    const payload = {
      name: title,
      department_id: deptId,
      priority: priority,
      duration_slots: duration,
      expected_attendance: attendance,
      required_venue_type: venueType,
      preferred_start_hour: prefHour,
      allowed_slots: (state && state.venues && state.venues[0]) ? state.venues[0].available_slots : [],
      required_staff_ids: [],
      required_equipment: {},
    };

    try {
      await api('/api/events', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      modal.classList.add('hidden');
      form.reset();
      clearError();

      // Refresh state and run solver automatically to place new event
      await refreshState();
      const sched = await api('/api/schedule/generate', {
        method: 'POST',
        body: JSON.stringify({ max_nodes: 100000 }),
      });
      renderTimetable(sched);

    } catch (err) {
      showError(err.message || 'Failed to add event');
    }
  });
}

async function deleteEvent(eventId) {
  if (!confirm('Are you sure you want to remove this event from the university schedule?')) return;
  try {
    await api(`/api/events/${eventId}`, { method: 'DELETE' });
    await refreshState();
    const sched = await api('/api/schedule/generate', {
      method: 'POST',
      body: JSON.stringify({ max_nodes: 100000 }),
    });
    renderTimetable(sched);
  } catch (err) {
    alert(`Could not delete event: ${err.message}`);
  }
}

// --------------------------------------------------------------------------
// Tab & SubTab Navigation
// --------------------------------------------------------------------------

// Maps the data-tab button value → the actual HTML pane element id
const TAB_PANE_MAP = {
  timetable:  'timetableTab',
  events:     'directoryTab',
  explainable: 'explainTab',
  whatif:     'whatifTab',
  analytics:  'analyticsTab',
};

let activeTab = 'timetable';

function setActiveTab(tabName) {
  if (!TAB_PANE_MAP[tabName]) return;
  activeTab = tabName;

  // Update button highlights
  document.querySelectorAll('.tab-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === tabName);
  });

  // Show only the matching pane, hide all others
  Object.entries(TAB_PANE_MAP).forEach(([name, paneId]) => {
    const pane = $(paneId);
    if (!pane) return;
    if (name === tabName) {
      pane.classList.add('active');
    } else {
      pane.classList.remove('active');
    }
  });

  // Side-effects per tab
  if (tabName === 'analytics') {
    loadAnalytics();
  }
}

function initTabs() {
  // Wire up main tab buttons
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      setActiveTab(btn.dataset.tab);
    });
  });

  // Wire up sub-tab buttons (Events & Resources inner nav)
  document.querySelectorAll('.sub-tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.sub-tab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.subtab-content').forEach((c) => c.classList.remove('active'));
      btn.classList.add('active');
      const content = $(btn.dataset.subtab);
      if (content) content.classList.add('active');
    });
  });

  if ($('explainEventSelect')) {
    $('explainEventSelect').addEventListener('change', (e) => {
      inspectEvent(parseInt(e.target.value, 10));
    });
  }

  if ($('heatmapToggle')) {
    $('heatmapToggle').addEventListener('change', () => {
      if (currentSchedule) renderTimetable(currentSchedule);
    });
  }

  // Set default tab on startup
  setActiveTab('timetable');
}

// --------------------------------------------------------------------------
// Core Actions: Demo Reset & Solve
// --------------------------------------------------------------------------
async function refreshState() {
  state = await api('/api/state');
  renderState();
}

async function initActions() {
  // What-If event & venue selection sync
  if ($('whatIfEventSelect')) $('whatIfEventSelect').addEventListener('change', syncWhatIfEventSelection);
  if ($('whatIfVenueSelect')) $('whatIfVenueSelect').addEventListener('change', syncWhatIfVenueSelection);
  if ($('whatIfTimeShiftEventSelect')) $('whatIfTimeShiftEventSelect').addEventListener('change', syncWhatIfTimeShiftSelection);
  if ($('whatIfUnavailableVenueSelect')) $('whatIfUnavailableVenueSelect').addEventListener('change', updateUnavailabilityReasonState);

  // Advanced controls accordion toggle
  if ($('toggleAdvancedControlsBtn')) $('toggleAdvancedControlsBtn').addEventListener('click', toggleAdvancedControls);

  // Reset What-If button
  if ($('resetWhatIfBtn')) $('resetWhatIfBtn').addEventListener('click', resetWhatIfSimulation);

  // Retry Simulation button
  if ($('retrySimulationBtn')) $('retrySimulationBtn').addEventListener('click', runWhatIfSimulation);

  // Clear Scenario History button
  if ($('clearHistoryBtn')) {
    $('clearHistoryBtn').addEventListener('click', () => {
      if (confirm('Are you sure you want to clear all scenario simulation history?')) {
        scenarioHistoryList = [];
        renderScenarioHistory();
      }
    });
  }

  // Load Demo Button
  $('demoBtn').addEventListener('click', async () => {
    const btn = $('demoBtn');
    btn.disabled = true;
    try {
      const data = await api('/api/demo/reset', { method: 'POST' });
      state = data.state;
      renderState();
      // Auto solve demo
      const sched = await api('/api/schedule/generate', {
        method: 'POST',
        body: JSON.stringify({ max_nodes: 100000 }),
      });
      renderTimetable(sched);
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  // Generate Schedule Button (Purple Primary)
  $('solveBtn').addEventListener('click', async () => {
    const btn = $('solveBtn');
    const textEl = $('solveBtnText');
    btn.disabled = true;
    textEl.textContent = 'Optimizing Matrix...';

    try {
      const result = await api('/api/schedule/generate', {
        method: 'POST',
        body: JSON.stringify({ max_nodes: 100000 }),
      });
      renderTimetable(result);
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
      textEl.textContent = 'Generate Schedule';
    }
  });

  // What-If Simulation
  $('runWhatIfBtn').addEventListener('click', runWhatIfSimulation);

  // Refresh Analytics Button
  $('refreshAnalyticsBtn').addEventListener('click', loadAnalytics);

  // OR-Tools Cross-Check Button
  $('runOrtollsBtn').addEventListener('click', runOrToolsBenchmark);
}

// --------------------------------------------------------------------------
// User Authentication & Profile UI
// --------------------------------------------------------------------------
let currentUser = null;

async function checkAuthSession() {
  try {
    const res = await fetch('/api/auth/me');
    if (!res.ok) {
      window.location.href = '/login';
      return false;
    }
    const data = await res.json();
    if (!data.authenticated || !data.user) {
      window.location.href = '/login';
      return false;
    }
    currentUser = data.user;
    if (data.csrf_token) {
      sessionStorage.setItem('lpu_csrf_token', data.csrf_token);
    }
    updateUserProfileUI(currentUser);
    return true;
  } catch (err) {
    console.warn('Auth check error:', err);
    return true;
  }
}

function updateUserProfileUI(user) {
  if (!user) return;
  const avatar = $('userAvatar');
  const nameEl = $('userDisplayName');
  const badgeEl = $('userRoleBadge');
  const menuName = $('menuFullName');
  const menuEmail = $('menuEmail');
  const menuRole = $('menuRoleName');
  const roleSel = $('roleSelector');

  if (avatar) avatar.textContent = (user.full_name || user.email || 'U')[0].toUpperCase();
  if (nameEl) nameEl.textContent = (user.full_name || '').split(' ')[0] || user.role;
  if (badgeEl) badgeEl.textContent = user.role;
  if (menuName) menuName.textContent = user.full_name || 'Institutional User';
  if (menuEmail) menuEmail.textContent = user.email || '';
  if (menuRole) menuRole.textContent = user.role;

  if (roleSel) {
    roleSel.value = user.role;
  }
}

function initUserProfile() {
  const btn = $('userProfileBtn');
  const menu = $('userProfileMenu');
  const logoutBtn = $('logoutBtn');

  if (btn && menu) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.classList.toggle('hidden');
    });

    document.addEventListener('click', () => {
      menu.classList.add('hidden');
    });
  }

  if (logoutBtn) {
    logoutBtn.addEventListener('click', async () => {
      try {
        const csrf = sessionStorage.getItem('lpu_csrf_token') || '';
        await fetch('/api/auth/logout', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
          },
        });
      } catch (e) {}
      sessionStorage.clear();
      window.location.href = '/login';
    });
  }
}

// --------------------------------------------------------------------------
// AI Assistant Frontend Client
// --------------------------------------------------------------------------
function formatMarkdown(text) {
  if (!text) return '';
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Headers
  html = html.replace(/^### (.*$)/gim, '<h4 style="margin: 8px 0 4px 0; color: #f58220; font-size: 0.95rem;">$1</h4>');
  html = html.replace(/^## (.*$)/gim, '<h3 style="margin: 8px 0 4px 0; color: #f58220; font-size: 1rem;">$1</h3>');

  // Bold & Italics
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

  // Code
  html = html.replace(/`(.*?)`/g, '<code>$1</code>');

  // Lists
  html = html.replace(/^\s*[-•]\s+(.*$)/gim, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>)/gms, '<ul style="margin: 6px 0; padding-left: 20px;">$1</ul>');

  // Paragraphs
  html = html.split('\n\n').map(p => {
    if (p.startsWith('<h') || p.startsWith('<ul')) return p;
    return `<p style="margin-bottom: 6px;">${p.replace(/\n/g, '<br/>')}</p>`;
  }).join('');

  return html;
}

let aiChatHistory = [];
let aiLLMSettings = {
  provider: 'auto',
  apiKey: '',
  model: '',
  baseUrl: '',
};

async function initAIAssistant() {
  const floatBtn = $('aiAssistantFloatBtn');
  const headerBtn = $('aiAssistantHeaderBtn');
  const chatWindow = $('aiChatWindow');
  const minimizeBtn = $('aiMinimizeBtn');
  const closeBtn = $('aiCloseBtn');
  const clearBtn = $('aiClearHistoryBtn');
  const settingsBtn = $('aiSettingsBtn');
  const settingsPanel = $('aiSettingsPanel');
  const settingsCloseBtn = $('aiSettingsCloseBtn');
  const providerSelect = $('aiProviderSelect');
  const apiKeyField = $('aiApiKeyField');
  const apiKeyInput = $('aiApiKeyInput');
  const toggleKeyBtn = $('aiToggleKeyBtn');
  const modelInput = $('aiModelInput');
  const baseUrlInput = $('aiBaseUrlInput');
  const testConnectionBtn = $('aiTestConnectionBtn');
  const saveSettingsBtn = $('aiSaveSettingsBtn');
  const connectionStatus = $('aiConnectionStatus');
  const presetChips = document.querySelectorAll('.ai-preset-chip');
  const form = $('aiChatForm');
  const input = $('aiChatInput');
  const promptsCarousel = $('aiPromptsCarousel');
  const messagesContainer = $('aiMessagesContainer');
  const typingIndicator = $('aiTypingIndicator');
  const providerBadge = $('aiProviderBadge');

  // Load saved LLM settings from localStorage
  const savedSettings = localStorage.getItem('lpu_llm_settings');
  if (savedSettings) {
    try {
      aiLLMSettings = { ...aiLLMSettings, ...JSON.parse(savedSettings) };
    } catch (e) {}
  }

  // Populate settings form with current state
  if (providerSelect) providerSelect.value = aiLLMSettings.provider || 'auto';
  if (apiKeyInput) apiKeyInput.value = aiLLMSettings.apiKey || '';
  if (modelInput) modelInput.value = aiLLMSettings.model || '';
  if (baseUrlInput) baseUrlInput.value = aiLLMSettings.baseUrl || '';

  const updateSettingVisibility = () => {
    const prov = providerSelect ? providerSelect.value : 'auto';
    if (apiKeyField) {
      if (prov === 'local' || prov === 'ollama') {
        apiKeyField.style.display = 'none';
      } else {
        apiKeyField.style.display = 'flex';
      }
    }
  };
  if (providerSelect) {
    providerSelect.addEventListener('change', () => {
      updateSettingVisibility();
      // Auto-suggest default model based on provider
      const prov = providerSelect.value;
      if (prov === 'gemini' && !modelInput.value) modelInput.value = 'gemini-2.0-flash';
      if (prov === 'openai' && !modelInput.value) modelInput.value = 'gpt-4o-mini';
      if (prov === 'ollama' && !modelInput.value) modelInput.value = 'llama3';
      if (prov === 'anthropic' && !modelInput.value) modelInput.value = 'claude-3-5-haiku-20241022';
    });
  }
  updateSettingVisibility();

  // Settings toggle
  if (settingsBtn && settingsPanel) {
    settingsBtn.addEventListener('click', () => {
      settingsPanel.classList.toggle('hidden');
    });
  }
  if (settingsCloseBtn && settingsPanel) {
    settingsCloseBtn.addEventListener('click', () => {
      settingsPanel.classList.add('hidden');
    });
  }

  // Toggle API key masking
  if (toggleKeyBtn && apiKeyInput) {
    toggleKeyBtn.addEventListener('click', () => {
      const isPwd = apiKeyInput.type === 'password';
      apiKeyInput.type = isPwd ? 'text' : 'password';
      toggleKeyBtn.textContent = isPwd ? '🙈' : '👁️';
    });
  }

  // Preset model chips
  presetChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const p = chip.getAttribute('data-provider');
      const m = chip.getAttribute('data-model');
      if (providerSelect && p) providerSelect.value = p;
      if (modelInput && m) modelInput.value = m;
      updateSettingVisibility();
    });
  });

  // Test connection button
  if (testConnectionBtn) {
    testConnectionBtn.addEventListener('click', async () => {
      testConnectionBtn.disabled = true;
      testConnectionBtn.textContent = 'Testing...';
      if (connectionStatus) {
        connectionStatus.className = 'ai-connection-status';
        connectionStatus.textContent = 'Testing connection...';
        connectionStatus.classList.remove('hidden');
      }

      try {
        const csrf = sessionStorage.getItem('lpu_csrf_token') || '';
        const res = await fetch('/api/ai/test-connection', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
          },
          body: JSON.stringify({
            provider: providerSelect ? providerSelect.value : 'auto',
            api_key: apiKeyInput ? apiKeyInput.value.trim() : null,
            model: modelInput ? modelInput.value.trim() : null,
            base_url: baseUrlInput ? baseUrlInput.value.trim() : null,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (connectionStatus) {
          if (res.ok && data.ok) {
            connectionStatus.className = 'ai-connection-status success';
            const lat = data.latency_ms ? ` (${data.latency_ms}ms)` : '';
            connectionStatus.textContent = `✅ Connected to ${data.provider} [${data.model}]${lat}: ${data.message}`;
          } else {
            connectionStatus.className = 'ai-connection-status error';
            connectionStatus.textContent = `❌ ${data.message || data.detail || 'Connection failed'}`;
          }
        }
      } catch (err) {
        if (connectionStatus) {
          connectionStatus.className = 'ai-connection-status error';
          connectionStatus.textContent = `❌ Network Error: ${err.message}`;
        }
      } finally {
        testConnectionBtn.disabled = false;
        testConnectionBtn.textContent = '⚡ Test Connection';
      }
    });
  }

  // Save Settings button
  if (saveSettingsBtn) {
    saveSettingsBtn.addEventListener('click', () => {
      aiLLMSettings = {
        provider: providerSelect ? providerSelect.value : 'auto',
        apiKey: apiKeyInput ? apiKeyInput.value.trim() : '',
        model: modelInput ? modelInput.value.trim() : '',
        baseUrl: baseUrlInput ? baseUrlInput.value.trim() : '',
      };
      localStorage.setItem('lpu_llm_settings', JSON.stringify(aiLLMSettings));
      if (providerBadge) {
        const provLabel = aiLLMSettings.provider === 'auto' ? 'Auto-Detect' : aiLLMSettings.provider;
        const modLabel = aiLLMSettings.model ? ` (${aiLLMSettings.model})` : '';
        providerBadge.textContent = `${provLabel}${modLabel}`;
      }
      if (settingsPanel) settingsPanel.classList.add('hidden');
      appendChatMessage('assistant', `⚙️ **LLM Settings Updated:** Provider configured to **${aiLLMSettings.provider.toUpperCase()}**${aiLLMSettings.model ? ` (${aiLLMSettings.model})` : ''}. All questions will be grounded with live schedule data.`, false);
    });
  }

  // Query server LLM config on startup to update header badge
  try {
    const cfgRes = await fetch('/api/ai/config');
    if (cfgRes.ok) {
      const cfg = await cfgRes.json();
      if (providerBadge && (!savedSettings || aiLLMSettings.provider === 'auto')) {
        providerBadge.textContent = `${cfg.active_provider.toUpperCase()} (${cfg.active_model})`;
      }
    }
  } catch (e) {}

  // Load and render suggested prompts
  try {
    const res = await fetch('/api/ai/suggested-prompts');
    if (res.ok) {
      const data = await res.json();
      if (promptsCarousel && data.prompts) {
        promptsCarousel.innerHTML = '';
        data.prompts.forEach(p => {
          const chip = document.createElement('button');
          chip.type = 'button';
          chip.className = 'ai-prompt-chip';
          chip.textContent = p;
          chip.addEventListener('click', () => {
            if (input) input.value = p;
            submitAIChat(p);
          });
          promptsCarousel.appendChild(chip);
        });
      }
    }
  } catch (err) {
    console.warn('Could not load AI prompts:', err);
  }

  // Restore session history from sessionStorage if available
  const savedHistory = sessionStorage.getItem('lpu_ai_history');
  if (savedHistory) {
    try {
      aiChatHistory = JSON.parse(savedHistory);
      aiChatHistory.forEach(item => {
        appendChatMessage(item.role, item.content, false);
      });
    } catch (e) {}
  }

  // Toggle open / close
  const toggleChat = () => {
    if (!chatWindow) return;
    const isHidden = chatWindow.classList.contains('hidden');
    if (isHidden) {
      chatWindow.classList.remove('hidden');
      chatWindow.classList.remove('minimized');
      if (input) input.focus();
    } else {
      chatWindow.classList.add('hidden');
    }
  };

  if (floatBtn) floatBtn.addEventListener('click', toggleChat);
  if (headerBtn) headerBtn.addEventListener('click', toggleChat);

  if (minimizeBtn) {
    minimizeBtn.addEventListener('click', () => {
      chatWindow.classList.toggle('minimized');
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      chatWindow.classList.add('hidden');
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      aiChatHistory = [];
      sessionStorage.removeItem('lpu_ai_history');
      if (messagesContainer) {
        messagesContainer.innerHTML = `
          <div class="ai-message-row assistant">
            <div class="ai-msg-avatar">🤖</div>
            <div class="ai-msg-bubble">
              <p><strong>Chat history cleared.</strong></p>
              <p>How may I assist you with university schedules or venue reservations today?</p>
            </div>
          </div>
        `;
      }
    });
  }

  function appendChatMessage(role, content, save = true) {
    if (!messagesContainer) return;
    const isUser = role === 'user';
    const row = document.createElement('div');
    row.className = `ai-message-row ${isUser ? 'user' : 'assistant'}`;

    const avatar = document.createElement('div');
    avatar.className = 'ai-msg-avatar';
    avatar.textContent = isUser ? '👤' : '🤖';

    const bubble = document.createElement('div');
    bubble.className = 'ai-msg-bubble';
    if (isUser) {
      bubble.textContent = content;
    } else {
      bubble.innerHTML = formatMarkdown(content);
    }

    row.appendChild(avatar);
    row.appendChild(bubble);
    messagesContainer.appendChild(row);

    const body = $('aiChatBody');
    if (body) body.scrollTop = body.scrollHeight;

    if (save) {
      aiChatHistory.push({ role, content });
      sessionStorage.setItem('lpu_ai_history', JSON.stringify(aiChatHistory));
    }
  }

  async function submitAIChat(query) {
    const text = (query || (input ? input.value : '')).trim();
    if (!text) return;
    if (input) input.value = '';

    appendChatMessage('user', text, true);

    if (typingIndicator) {
      typingIndicator.classList.remove('hidden');
      const body = $('aiChatBody');
      if (body) body.scrollTop = body.scrollHeight;
    }

    const sendBtn = $('aiSendBtn');
    if (sendBtn) sendBtn.disabled = true;

    try {
      const csrf = sessionStorage.getItem('lpu_csrf_token') || '';
      const payload = {
        message: text,
        history: aiChatHistory.slice(-6),
        provider: aiLLMSettings.provider || 'auto',
        api_key: aiLLMSettings.apiKey || null,
        model: aiLLMSettings.model || null,
        base_url: aiLLMSettings.baseUrl || null,
      };

      const res = await fetch('/api/ai/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
        },
        body: JSON.stringify(payload),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        throw new Error(data.detail || 'AI Assistant encountered an error.');
      }

      if (providerBadge && data.provider) {
        providerBadge.textContent = data.provider;
      }

      appendChatMessage('assistant', data.reply, true);
    } catch (err) {
      appendChatMessage('assistant', `⚠️ **Error:** ${err.message}`, true);
    } finally {
      if (typingIndicator) typingIndicator.classList.add('hidden');
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      submitAIChat();
    });
  }
}

// --------------------------------------------------------------------------
// App Initialization
// --------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  initTabs();
  initTimetableControls();
  initPillInteractions();
  initEventModal();
  await initActions();

  // Initialize Authentication & Profile
  await checkAuthSession();
  initUserProfile();
  await initAIAssistant();

  try {
    await refreshState();
    // Try to load existing schedule or solve initial demo
    const existing = await api('/api/schedule').catch(() => null);
    if (existing) {
      renderTimetable(existing);
    } else {
      const initial = await api('/api/schedule/generate', {
        method: 'POST',
        body: JSON.stringify({ max_nodes: 100000 }),
      });
      renderTimetable(initial);
    }
  } catch (e) {
    console.error('Startup initialization error:', e);
  }
});
