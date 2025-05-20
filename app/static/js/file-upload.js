/**
 * File xử lý các tương tác tải lên tệp
 */

// Hàm cập nhật tên file khi người dùng chọn file
function updateFileName(input, displayElementId) {
  const displayElement = document.getElementById(displayElementId);
  if (!displayElement) return;

  if (input.files && input.files[0]) {
    const fileName = input.files[0].name;
    const fileExtension = fileName.split(".").pop().toLowerCase();
    let iconClass = "bi-file-earmark";
    let colorClass = "";

    // Xác định icon dựa trên loại file
    if (["xlsx", "xls"].includes(fileExtension)) {
      iconClass = "bi-file-earmark-excel";
      colorClass = "file-type-excel";
    } else if (["txt"].includes(fileExtension)) {
      iconClass = "bi-file-earmark-text";
      colorClass = "file-type-text";
    } else if (["csv"].includes(fileExtension)) {
      iconClass = "bi-filetype-csv";
      colorClass = "file-type-csv";
    } else if (["jpg", "jpeg", "png", "gif", "webp"].includes(fileExtension)) {
      iconClass = "bi-file-earmark-image";
      colorClass = "file-type-image";
    }

    // Hiển thị thông tin file
    displayElement.innerHTML = `
            <div class="selected-file-info">
                <i class="bi ${iconClass} ${colorClass}"></i>
                <div>
                    <strong>Đã chọn file:</strong> ${fileName}
                </div>
            </div>
        `;

    // Hiệu ứng hiển thị
    setTimeout(() => {
      const infoElement = displayElement.querySelector(".selected-file-info");
      if (infoElement) infoElement.classList.add("show");
    }, 10);
  } else {
    displayElement.innerHTML = "";
  }
}

// Hàm cập nhật tên file khi người dùng chọn nhiều file
function updateFileNameMultiple(input, displayElementId) {
  const displayElement = document.getElementById(displayElementId);
  if (!displayElement) return;

  if (input.files && input.files.length > 0) {
    let fileListHTML = "";

    // Hiển thị tối đa 5 file, nếu có nhiều hơn thì hiển thị dạng "... và N file khác"
    const maxFiles = 5;
    const totalFiles = input.files.length;

    for (let i = 0; i < Math.min(maxFiles, totalFiles); i++) {
      const fileName = input.files[i].name;
      const fileExtension = fileName.split(".").pop().toLowerCase();
      let iconClass = "bi-file-earmark";
      let colorClass = "";

      // Xác định icon dựa trên loại file
      if (["xlsx", "xls"].includes(fileExtension)) {
        iconClass = "bi-file-earmark-excel";
        colorClass = "file-type-excel";
      } else if (["txt"].includes(fileExtension)) {
        iconClass = "bi-file-earmark-text";
        colorClass = "file-type-text";
      } else if (["csv"].includes(fileExtension)) {
        iconClass = "bi-filetype-csv";
        colorClass = "file-type-csv";
      } else if (
        ["jpg", "jpeg", "png", "gif", "webp"].includes(fileExtension)
      ) {
        iconClass = "bi-file-earmark-image";
        colorClass = "file-type-image";
      }

      fileListHTML += `
                <li><i class="bi ${iconClass} ${colorClass} file-type-icon"></i>${fileName}</li>
            `;
    }

    if (totalFiles > maxFiles) {
      fileListHTML += `<li>... và ${totalFiles - maxFiles} file khác</li>`;
    }

    // Hiển thị thông tin file
    displayElement.innerHTML = `
            <div class="selected-file-info">
                <i class="bi bi-files"></i>
                <div>
                    <strong>Đã chọn ${totalFiles} file:</strong>
                    <ul class="selected-files-list">
                        ${fileListHTML}
                    </ul>
                </div>
            </div>
        `;

    // Hiệu ứng hiển thị
    setTimeout(() => {
      const infoElement = displayElement.querySelector(".selected-file-info");
      if (infoElement) infoElement.classList.add("show");
    }, 10);
  } else {
    displayElement.innerHTML = "";
  }
}

// Kích hoạt tab dựa trên URL hash hoặc session
function activateTabFromSession() {
  // Kiểm tra hash trong URL
  const hash = window.location.hash;
  if (hash) {
    const tabId = hash.replace("#", "");
    activateTab(tabId);
  } else {
    // Kiểm tra tab đang active từ session (được xử lý từ backend)
    const activeTabElement = document.querySelector(".nav-link.active");
    if (activeTabElement) {
      const tabId = activeTabElement.id;
      activateTab(tabId);
    }
  }
}

// Hàm kích hoạt tab dựa trên ID
function activateTab(tabId) {
  // Loại bỏ class active từ tất cả các tab
  document.querySelectorAll(".nav-link").forEach((tab) => {
    tab.classList.remove("active");
    tab.setAttribute("aria-selected", "false");
  });

  // Loại bỏ class show và active từ tất cả các tab-pane
  document.querySelectorAll(".tab-pane").forEach((pane) => {
    pane.classList.remove("show", "active");
  });

  // Thêm class active cho tab được chọn
  const selectedTab = document.getElementById(tabId);
  if (selectedTab) {
    selectedTab.classList.add("active");
    selectedTab.setAttribute("aria-selected", "true");

    // Tìm và kích hoạt tab-pane tương ứng
    const targetId = selectedTab.getAttribute("data-bs-target");
    const tabContent = document.querySelector(targetId);
    if (tabContent) {
      tabContent.classList.add("show", "active");
    }
  }
}

// Kiểm tra và kích hoạt tab khi trang đã tải xong
document.addEventListener("DOMContentLoaded", function () {
  activateTabFromSession();

  // Thêm sự kiện click cho các tab
  document.querySelectorAll(".nav-link").forEach((tab) => {
    tab.addEventListener("click", function () {
      const tabId = this.id;
      // Cập nhật URL hash khi click tab
      window.location.hash = tabId;
    });
  });
});
