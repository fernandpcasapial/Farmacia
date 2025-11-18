// app.js - JavaScript functionality for the web app

let state = {
  page: 1,
  pages: 0,
  per: 25,
  sort_col: "Precio",
  sort_asc: true,
  pharm_sel: [],
  min_link: null,
  max_link: null,
  rows: []
};

// DOM elements
const q = document.querySelector("#q");
const scope = document.querySelector("#scope");
const mode = document.querySelector("#mode");
const per = document.querySelector("#per");
const btnSearch = document.querySelector("#btnSearch");
const tblBody = document.querySelector("#tbl tbody");
const pharmChips = document.querySelector("#pharmChips");
const pharmacySelectors = document.querySelector("#pharmacySelectors");

// Available pharmacies (loaded from backend)
let availablePharmacies = [];
let selectedPharmaciesForSearch = [];

function setPharmacies(list) {
  pharmChips.innerHTML = "";
  list.forEach(p => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.textContent = p;
    chip.onclick = () => {
      if (state.pharm_sel.includes(p)) {
        state.pharm_sel = state.pharm_sel.filter(x => x !== p);
        chip.classList.remove("sel");
      } else {
        if (state.pharm_sel.length >= 4) return;
        state.pharm_sel.push(p);
        chip.classList.add("sel");
      }
      state.page = 1;
      loadPage();
    };
    pharmChips.appendChild(chip);
  });
}

async function loadAvailablePharmacies() {
  try {
    const r = await fetch("/api/pharmacies");
    const j = await r.json();
    availablePharmacies = j.pharmacies || [];
    renderPharmacySelectors();
  } catch (error) {
    console.error("Error loading pharmacies:", error);
  }
}

function renderPharmacySelectors() {
  if (!pharmacySelectors) return;
  
  pharmacySelectors.innerHTML = "";
  availablePharmacies.forEach(pharmacy => {
    const checkboxDiv = document.createElement("div");
    checkboxDiv.className = "pharmacy-checkbox";
    checkboxDiv.innerHTML = `
      <input type="checkbox" id="pharm_${pharmacy.name}" value="${pharmacy.name}" checked>
      <label for="pharm_${pharmacy.name}">${pharmacy.name}</label>
    `;
    checkboxDiv.querySelector("input").addEventListener("change", updateSelectedPharmacies);
    pharmacySelectors.appendChild(checkboxDiv);
  });
  updateSelectedPharmacies();
}

function updateSelectedPharmacies() {
  if (!pharmacySelectors) return;
  const checkboxes = pharmacySelectors.querySelectorAll("input[type='checkbox']");
  selectedPharmaciesForSearch = Array.from(checkboxes)
    .filter(cb => cb.checked)
    .map(cb => cb.value);
}

async function search() {
  const text = q.value.trim();
  if (!text) {
    alert("Escribe primero el producto a buscar.");
    return;
  }
  
  // Show progress indicator for web searches
  if (mode.value === "web" || mode.value === "both") {
    showProgressIndicator();
  }
  
  try {
    const url = new URL("/api/search", location.origin);
    url.searchParams.set("q", text);
    url.searchParams.set("scope", scope.value);
    url.searchParams.set("mode", mode.value);
    // Agregar farmacias seleccionadas solo si está en modo WEB/AMBOS y hay selección
    // Si no hay selección, el backend buscará en todas las farmacias por defecto
    if ((mode.value === "web" || mode.value === "both") && selectedPharmaciesForSearch.length > 0) {
      selectedPharmaciesForSearch.forEach(pharm => {
        url.searchParams.append("pharmacy", pharm);
      });
    }
    const r = await fetch(url);
    const j = await r.json();
    setPharmacies(j.pharmacies || []);
    state.page = 1;
    state.pharm_sel = [];
    state.rows = j.rows || [];
    renderChipSelection();
    document.querySelector("#lastMods").textContent = "BASE: " + (j.base_last||"—") + " · EXTRA: " + (j.extra_last||"—");
    await loadPage();
    
    // Show CRUD buttons if admin and has results
    const crudButtons = document.querySelector("#crudButtons");
    if (crudButtons && j.rows && j.rows.length > 0) {
      crudButtons.style.display = "flex";
    }
  } catch (error) {
    console.error("Search error:", error);
    alert("Error en la búsqueda. Intenta de nuevo.");
  } finally {
    hideProgressIndicator();
  }
}

function showProgressIndicator() {
  // Create progress indicator if it doesn't exist
  let progressDiv = document.querySelector("#searchProgress");
  const pharmaciesText = selectedPharmaciesForSearch.length > 0 
    ? selectedPharmaciesForSearch.join(', ') 
    : 'todas las farmacias';
  
  if (!progressDiv) {
    progressDiv = document.createElement("div");
    progressDiv.id = "searchProgress";
    document.body.appendChild(progressDiv);
  }
  
  progressDiv.innerHTML = `
    <div style="
      position: fixed;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      background: rgba(0,0,0,0.9);
      color: white;
      padding: 25px 35px;
      border-radius: 15px;
      z-index: 10000;
      text-align: center;
      font-family: ui-sans-serif, system-ui;
      box-shadow: 0 10px 40px rgba(0,0,0,0.5);
      border: 1px solid rgba(29,209,161,0.3);
    ">
      <div style="margin-bottom: 20px;">
        <div style="
          width: 50px;
          height: 50px;
          border: 4px solid #1dd1a1;
          border-top: 4px solid transparent;
          border-radius: 50%;
          animation: spin 1s linear infinite;
          margin: 0 auto 15px;
        "></div>
      </div>
      <div style="font-size: 18px; font-weight: 600; margin-bottom: 10px; color: #1dd1a1;">
        🔍 Buscando en farmacias peruanas...
      </div>
      <div style="font-size: 14px; color: #ccc; margin-bottom: 15px;">
        Revisando ${pharmaciesText}...
      </div>
      <div style="font-size: 12px; color: #999;">
        Esto puede tomar unos segundos
      </div>
    </div>
    <style>
      @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }
    </style>
  `;
  progressDiv.style.display = "block";
}

function hideProgressIndicator() {
  const progressDiv = document.querySelector("#searchProgress");
  if (progressDiv) {
    progressDiv.style.display = "none";
  }
}

function renderChipSelection() {
  [...pharmChips.children].forEach(ch => {
    ch.classList.toggle("sel", state.pharm_sel.includes(ch.textContent));
  });
}

function sortBy(col) {
  if (state.sort_col === col) state.sort_asc = !state.sort_asc;
  else { state.sort_col = col; state.sort_asc = true; }
  state.page = 1;
  loadPage();
}

async function loadPage() {
  const params = new URLSearchParams();
  params.set("page", state.page);
  params.set("per", state.per);
  params.set("sort_col", state.sort_col);
  params.set("sort_asc", state.sort_asc);
  state.pharm_sel.forEach(p => params.append("pharmacy", p));
  const r = await fetch("/api/view?" + params.toString());
  const j = await r.json();

  tblBody.innerHTML = "";
  (j.rows || []).forEach((row, pageIndex) => {
    const tr = document.createElement("tr");
    // Guardar el índice real en state.rows usando los datos de la fila
    tr.dataset.rowKey = JSON.stringify({
      codigo: row["CÓDIGO PRODUCTO"] || row["N° DIGEMID"] || "",
      producto: row["Producto (Marca comercial)"] || "",
      digemid: row["N° DIGEMID"] || "",
      origen: row["_ORIGEN"] || ""
    });
    tr.innerHTML = `
      <td>${(row["Producto (Marca comercial)"] || "").toUpperCase()}</td>
      <td>${(row["Principio Activo"] || "").toUpperCase()}</td>
      <td>${(row["Presentación"] || "").toUpperCase()}</td>
      <td class="price">${row["Precio"] || ""}</td>
      <td>${(row["Laboratorio / Fabricante"] || "").toUpperCase()}</td>
      <td>${row["Farmacia / Fuente"] || ""}</td>
      <td class="rowlink"><a href="${row["Enlace"] || "#"}" target="_blank">Abrir</a></td>
      <td>${(row["GRUPO"] || "").toUpperCase()}</td>
    `;
    tblBody.appendChild(tr);
  });

  document.querySelector("#kpiCount").textContent = (j.total||0) + " resultado(s)";
  document.querySelector("#kpiPage").textContent = "Pág " + (j.page||0) + "/" + (j.pages||0);

  // min/max
  const min = j.min_item, max = j.max_item;
  const fmt = r => r ? `MENOR: ${r["Precio"]} | ${r["Farmacia / Fuente"]} — ${(r["Producto (Marca comercial)"]||"").toUpperCase()}` : "MENOR: —";
  const fmx = r => r ? `MAYOR: ${r["Precio"]} | ${r["Farmacia / Fuente"]} — ${(r["Producto (Marca comercial)"]||"").toUpperCase()}` : "MAYOR: —";
  document.querySelector("#kpiMin").textContent = fmt(min);
  document.querySelector("#kpiMax").textContent = fmx(max);
  const btnMin = document.querySelector("#btnOpenMin");
  const btnMax = document.querySelector("#btnOpenMax");
  state.min_link = min ? min["Enlace"] : null;
  state.max_link = max ? max["Enlace"] : null;
  btnMin.style.display = state.min_link ? "" : "none";
  btnMax.style.display = state.max_link ? "" : "none";

  // actualizar chips si hubiera nuevos
  setPharmacies(j.all_pharmacies || []);
  renderChipSelection();

  state.pages = j.pages || 0;
  state.page  = j.page || 1;
}

// Event listeners
document.querySelectorAll("th[data-col]").forEach(th => {
  th.addEventListener("click", () => sortBy(th.dataset.col));
});

document.querySelector("#btnPrev").onclick = () => {
  if (state.page > 1) { state.page--; loadPage(); }
};
document.querySelector("#btnNext").onclick = () => {
  if (state.page < state.pages) { state.page++; loadPage(); }
};
document.querySelector("#btnOpenMin").onclick = () => {
  if (state.min_link) window.open(state.min_link, "_blank");
};
document.querySelector("#btnOpenMax").onclick = () => {
  if (state.max_link) window.open(state.max_link, "_blank");
};
document.querySelector("#btnCsv").onclick = () => {
  const params = new URLSearchParams();
  state.pharm_sel.forEach(p => params.append("pharmacy", p));
  params.set("sort_col", state.sort_col);
  params.set("sort_asc", state.sort_asc);
  params.set("fmt","csv");
  window.open("/api/export?" + params.toString(), "_blank");
};
document.querySelector("#btnXlsx").onclick = () => {
  const params = new URLSearchParams();
  state.pharm_sel.forEach(p => params.append("pharmacy", p));
  params.set("sort_col", state.sort_col);
  params.set("sort_asc", state.sort_asc);
  params.set("fmt","xlsx");
  window.open("/api/export?" + params.toString(), "_blank");
};

btnSearch.onclick = search;
q.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
per.onchange = () => { state.per = parseInt(per.value,10)||25; state.page=1; loadPage(); };

// Load pharmacies on page load
loadAvailablePharmacies();

// Admin forms
const formBase  = document.querySelector("#formBase");
const formExtra = document.querySelector("#formExtra");
const formLogo  = document.querySelector("#formLogo");
if (formBase) {
  formBase.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(formBase);
    const r = await fetch("/api/admin/upload_base?which=main", { method:"POST", body:fd });
    const j = await r.json();
    alert(j.ok ? "BASE cargada." : ("Error: "+(j.error||"")));
  };
}
if (formExtra) {
  formExtra.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(formExtra);
    const r = await fetch("/api/admin/upload_base?which=extra", { method:"POST", body:fd });
    const j = await r.json();
    alert(j.ok ? "EXTRA cargada." : ("Error: "+(j.error||"")));
  };
}
if (formLogo) {
  formLogo.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(formLogo);
    const r = await fetch("/api/admin/upload_logo", { method:"POST", body:fd });
    const j = await r.json();
    if (j.ok) {
      alert("Logo actualizado.");
      setTimeout(()=>location.reload(), 400);
    } else {
      alert("Error: "+(j.error||""));
    }
  };
}

// CRUD Functions
const btnAdd = document.querySelector("#btnAdd");
const btnEdit = document.querySelector("#btnEdit");
const btnDelete = document.querySelector("#btnDelete");

if (btnAdd) {
  btnAdd.onclick = () => showEditDialog();
}
if (btnEdit) {
  btnEdit.onclick = () => editSelectedRow();
}
if (btnDelete) {
  btnDelete.onclick = () => deleteSelectedRow();
}

// User Management
const btnManageUsers = document.querySelector("#btnManageUsers");
if (btnManageUsers) {
  btnManageUsers.onclick = () => showUserManagement();
}

function getSelectedRowIndex() {
  const selectedRow = document.querySelector("#tbl tbody tr.selected");
  if (!selectedRow) return -1;
  return Array.from(selectedRow.parentNode.children).indexOf(selectedRow);
}

function getSelectedRowData() {
  const selectedRow = document.querySelector("#tbl tbody tr.selected");
  if (!selectedRow) return null;
  
  // Obtener los datos de la fila usando el dataset
  const rowKey = selectedRow.dataset.rowKey;
  if (!rowKey) return null;
  
  try {
    const key = JSON.parse(rowKey);
    const rows = state.rows || [];
    
    // Buscar la fila en state.rows usando los identificadores
    for (const row of rows) {
      const rowCodigo = (row["CÓDIGO PRODUCTO"] || row["N° DIGEMID"] || "").toString().trim();
      const rowProducto = (row["Producto (Marca comercial)"] || "").toString().trim().toUpperCase();
      const rowDigemid = (row["N° DIGEMID"] || "").toString().trim();
      const rowOrigen = row["_ORIGEN"] || "";
      
      const keyCodigo = (key.codigo || "").toString().trim();
      const keyProducto = (key.producto || "").toString().trim().toUpperCase();
      const keyDigemid = (key.digemid || "").toString().trim();
      const keyOrigen = key.origen || "";
      
      // Comparar usando múltiples campos para mayor precisión
      if (rowCodigo && keyCodigo && rowCodigo === keyCodigo) {
        return row;
      }
      if (rowDigemid && keyDigemid && rowDigemid === keyDigemid) {
        return row;
      }
      if (rowProducto && keyProducto && rowProducto === keyProducto && rowOrigen === keyOrigen) {
        return row;
      }
    }
    
    // Si no se encuentra, intentar usar el índice de la página (fallback)
    const pageIndex = getSelectedRowIndex();
    if (pageIndex >= 0) {
      // Calcular el índice real considerando la paginación
      const startIndex = (state.page - 1) * state.per;
      const realIndex = startIndex + pageIndex;
      if (realIndex < rows.length) {
        return rows[realIndex];
      }
    }
    
    return null;
  } catch (e) {
    console.error("Error parsing row key:", e);
    return null;
  }
}

function showEditDialog(data = null) {
  const isEdit = data !== null;
  const title = isEdit ? "Editar Registro" : "Agregar Registro";
  
  // Guardar los datos originales para identificar la fila en el backend
  const originalData = data ? {...data} : null;
  
  const dialog = document.createElement("div");
  dialog.className = "modal-overlay";
  dialog.innerHTML = `
    <div class="modal-content">
      <h3>${title}</h3>
      <form id="editForm">
        <div class="form-grid">
          <label>CÓDIGO PRODUCTO:</label>
          <input type="text" name="CÓDIGO PRODUCTO" value="${data ? (data["CÓDIGO PRODUCTO"] || "") : ""}">
          
          <label>Producto (Marca comercial):</label>
          <input type="text" name="Producto (Marca comercial)" value="${data ? (data["Producto (Marca comercial)"] || "") : ""}">
          
          <label>Principio Activo:</label>
          <input type="text" name="Principio Activo" value="${data ? (data["Principio Activo"] || "") : ""}">
          
          <label>N° DIGEMID:</label>
          <input type="text" name="N° DIGEMID" value="${data ? (data["N° DIGEMID"] || "") : ""}">
          
          <label>Laboratorio / Fabricante:</label>
          <input type="text" name="Laboratorio / Fabricante" value="${data ? (data["Laboratorio / Fabricante"] || "") : ""}">
          
          <label>Presentación:</label>
          <input type="text" name="Presentación" value="${data ? (data["Presentación"] || "") : ""}">
          
          <label>Precio:</label>
          <input type="text" name="Precio" value="${data ? (data["Precio"] || "") : ""}">
          
          <label>Farmacia / Fuente:</label>
          <input type="text" name="Farmacia / Fuente" value="${data ? (data["Farmacia / Fuente"] || "") : ""}">
          
          <label>Enlace:</label>
          <input type="text" name="Enlace" value="${data ? (data["Enlace"] || "") : ""}">
          
          <label>GRUPO:</label>
          <input type="text" name="GRUPO" value="${data ? (data["GRUPO"] || "") : ""}">
          
          <label>Laboratorio Abreviado:</label>
          <input type="text" name="Laboratorio Abreviado" value="${data ? (data["Laboratorio Abreviado"] || "") : ""}">
          
          <label>LABORATORIO PRECIO:</label>
          <input type="text" name="LABORATORIO PRECIO" value="${data ? (data["LABORATORIO PRECIO"] || "") : ""}">
        </div>
        <div class="modal-buttons">
          <button type="submit">${isEdit ? "Actualizar" : "Agregar"}</button>
          <button type="button" onclick="this.closest('.modal-overlay').remove()">Cancelar</button>
        </div>
      </form>
    </div>
  `;
  
  document.body.appendChild(dialog);
  
  const form = dialog.querySelector("#editForm");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const formData = new FormData(form);
    const formDataObj = Object.fromEntries(formData.entries());
    
    try {
      const url = isEdit ? "/api/admin/edit_row" : "/api/admin/add_row";
      let payload;
      
      if (isEdit && originalData) {
        // Para editar, usar los datos originales para identificar la fila
        payload = {
          ...formDataObj,
          // Enviar identificadores originales para encontrar la fila en el Excel
          original_codigo: (originalData["CÓDIGO PRODUCTO"] || originalData["N° DIGEMID"] || "").toString().trim(),
          original_producto: (originalData["Producto (Marca comercial)"] || "").toString().trim(),
          original_digemid: (originalData["N° DIGEMID"] || "").toString().trim()
        };
      } else {
        payload = formDataObj;
      }
      
      const r = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      
      const j = await r.json();
      if (j.ok) {
        alert(j.message);
        dialog.remove();
        await search();
      } else {
        alert("Error: " + (j.error || "No se pudo completar la operación"));
      }
    } catch (error) {
      alert("Error: " + error.message);
    }
  };
}

function editSelectedRow() {
  const rowData = getSelectedRowData();
  if (!rowData) {
    alert("Selecciona una fila para editar.");
    return;
  }
  
  // Solo se pueden editar registros de BASE
  if (rowData._ORIGEN && rowData._ORIGEN !== "BASE") {
    alert("Solo se pueden editar registros de la base principal (BASE).");
    return;
  }
  
  showEditDialog(rowData);
}

function deleteSelectedRow() {
  const rowData = getSelectedRowData();
  if (!rowData) {
    alert("Selecciona una fila para eliminar.");
    return;
  }
  
  // Solo se pueden eliminar registros de BASE
  if (rowData._ORIGEN && rowData._ORIGEN !== "BASE") {
    alert("Solo se pueden eliminar registros de la base principal (BASE).");
    return;
  }
  
  if (!confirm("¿Estás seguro de que quieres eliminar este registro?")) {
    return;
  }
  
  // Enviar identificadores para encontrar la fila en el Excel
  const payload = {
    codigo: rowData["CÓDIGO PRODUCTO"] || rowData["N° DIGEMID"],
    producto: rowData["Producto (Marca comercial)"],
    digemid: rowData["N° DIGEMID"]
  };
  
  fetch("/api/admin/delete_row", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  })
  .then(r => r.json())
  .then(j => {
    if (j.ok) {
      alert(j.message);
      search();
    } else {
      alert("Error: " + (j.error || ""));
    }
  })
  .catch(error => alert("Error: " + error.message));
}

function showUserManagement() {
  const dialog = document.createElement("div");
  dialog.className = "modal-overlay";
  dialog.innerHTML = `
    <div class="modal-content" style="width: 600px;">
      <h3>Gestión de Usuarios</h3>
      <div class="user-management">
        <div class="user-list">
          <h4>Usuarios Actuales</h4>
          <div id="userList"></div>
        </div>
        <div class="user-form">
          <h4>Agregar Usuario</h4>
          <form id="userForm">
            <input type="text" name="username" placeholder="Usuario" required>
            <input type="password" name="password" placeholder="Contraseña" required>
            <select name="role" required>
              <option value="consulta">Consulta</option>
              <option value="admin">Admin</option>
            </select>
            <button type="submit">Agregar Usuario</button>
          </form>
        </div>
      </div>
      <div class="modal-buttons">
        <button onclick="this.closest('.modal-overlay').remove()">Cerrar</button>
      </div>
    </div>
  `;
  
  document.body.appendChild(dialog);
  
  // Load users
  loadUsers();
  
  // Handle form submission
  const form = dialog.querySelector("#userForm");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const formData = new FormData(form);
    const data = Object.fromEntries(formData.entries());
    
    try {
      const r = await fetch("/api/admin/users", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(data)
      });
      
      const j = await r.json();
      if (j.ok) {
        alert(j.message);
        form.reset();
        loadUsers();
      } else {
        alert("Error: " + (j.error || ""));
      }
    } catch (error) {
      alert("Error: " + error.message);
    }
  };
  
  async function loadUsers() {
    try {
      const r = await fetch("/api/admin/users");
      const j = await r.json();
      const userList = dialog.querySelector("#userList");
      
      userList.innerHTML = j.users.map(u => `
        <div class="user-item">
          <span>${u.username} (${u.role})</span>
          <div class="user-actions">
            <button onclick="editUser('${u.username}')">Editar</button>
            <button onclick="deleteUser('${u.username}')" ${u.username === 'admin' ? 'disabled' : ''}>Eliminar</button>
          </div>
        </div>
      `).join('');
    } catch (error) {
      console.error("Error loading users:", error);
    }
  }
  
  // Make functions available globally for this dialog
  window.editUser = (username) => {
    const newPassword = prompt("Nueva contraseña (dejar vacío para no cambiar):");
    const newRole = prompt("Nuevo rol (admin/consulta):");
    
    if (newRole && ["admin", "consulta"].includes(newRole)) {
      fetch(`/api/admin/users/${username}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({password: newPassword, role: newRole})
      })
      .then(r => r.json())
      .then(j => {
        if (j.ok) {
          alert(j.message);
          loadUsers();
        } else {
          alert("Error: " + (j.error || ""));
        }
      });
    }
  };
  
  window.deleteUser = (username) => {
    if (confirm(`¿Eliminar usuario ${username}?`)) {
      fetch(`/api/admin/users/${username}`, {method: "DELETE"})
      .then(r => r.json())
      .then(j => {
        if (j.ok) {
          alert(j.message);
          loadUsers();
        } else {
          alert("Error: " + (j.error || ""));
        }
      });
    }
  };
}

// Add row selection functionality
document.addEventListener("DOMContentLoaded", () => {
  const tbody = document.querySelector("#tbl tbody");
  if (tbody) {
    tbody.addEventListener("click", (e) => {
      const row = e.target.closest("tr");
      if (row) {
        // Remove previous selection
        tbody.querySelectorAll("tr").forEach(r => r.classList.remove("selected"));
        // Add selection to clicked row
        row.classList.add("selected");
      }
    });
  }
});

