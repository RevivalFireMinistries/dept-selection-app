// State
let categories = [];
let uncategorized = [];
let maxDepartments = 3;
let selectedDepartments = [];

// Initialize on page load
document.addEventListener('DOMContentLoaded', loadData);

async function loadData() {
    try {
        const [deptRes, settingsRes] = await Promise.all([
            fetch('/api/departments').then(r => r.json()),
            fetch('/api/settings').then(r => r.json())
        ]);

        categories = deptRes.categories || [];
        uncategorized = deptRes.uncategorized || [];
        maxDepartments = parseInt(settingsRes.maxDepartments) || 3;

        // Update UI
        updateCounter();
        updateSelectionInfo();

        // Hide loading, show form
        document.getElementById('loadingState').classList.add('hidden');
        document.getElementById('formContent').classList.remove('hidden');

        renderDepartments();
    } catch (error) {
        console.error('Failed to load data:', error);
        showError('Failed to load departments. Please refresh the page.');
    }
}

function renderDepartments() {
    const container = document.getElementById('departmentsContainer');
    let html = '';

    // Categorized departments
    categories.forEach(category => {
        const maxSel = category.maxSelections || 1;
        const badgeText = maxSel === 1 ? 'pick one' : `pick up to ${maxSel}`;
        const badgeColor = maxSel === 1 ? 'bg-amber-100 text-amber-700' : 'bg-purple-100 text-purple-700';

        html += `
            <div class="space-y-3">
                <div class="flex items-center gap-2">
                    <h4 class="font-medium text-gray-900">${category.name}</h4>
                    <span class="px-2 py-0.5 ${badgeColor} text-xs rounded-full">${badgeText}</span>
                </div>
                <div class="grid grid-cols-2 gap-2">
                    ${category.departments.map(dept => renderDepartmentButton(dept, category)).join('')}
                </div>
            </div>
        `;
    });

    // Uncategorized departments
    if (uncategorized.length > 0) {
        html += `
            <div class="space-y-3">
                <div class="flex items-center gap-2">
                    <h4 class="font-medium text-gray-900">Other Departments</h4>
                    <span class="px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded-full">pick any</span>
                </div>
                <div class="grid grid-cols-2 gap-2">
                    ${uncategorized.map(dept => renderDepartmentButton(dept, null)).join('')}
                </div>
            </div>
        `;
    }

    container.innerHTML = html;
}

function renderDepartmentButton(dept, category) {
    const isSelected = selectedDepartments.includes(dept.id);

    // Check if this department can be selected
    let isDisabled = false;
    if (!isSelected) {
        // Check global max
        if (selectedDepartments.length >= maxDepartments) {
            isDisabled = true;
        }
        // Check category max if applicable
        else if (category) {
            const categoryDeptIds = category.departments.map(d => d.id);
            const selectedInCategory = selectedDepartments.filter(id => categoryDeptIds.includes(id)).length;
            if (selectedInCategory >= (category.maxSelections || 1)) {
                isDisabled = true;
            }
        }
    }

    const baseClass = 'relative px-4 py-3 rounded-xl text-sm font-medium transition-all duration-200';
    const selectedClass = 'bg-gradient-to-r from-indigo-600 to-purple-600 text-white shadow-lg scale-[1.02]';
    const normalClass = 'bg-gray-100 text-gray-700 hover:bg-gray-200 active:scale-[0.98]';
    const disabledClass = 'bg-gray-100 text-gray-400 cursor-not-allowed';

    let className = baseClass + ' ';
    if (isSelected) {
        className += selectedClass;
    } else if (isDisabled) {
        className += disabledClass;
    } else {
        className += normalClass;
    }

    const categoryId = category ? category.id : 'null';

    return `
        <button
            onclick="toggleDepartment(${dept.id}, ${categoryId})"
            class="${className}"
            ${isDisabled ? 'disabled' : ''}>
            ${dept.name}
            ${isSelected ? `
                <svg class="absolute top-1 right-1 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"></path>
                </svg>
            ` : ''}
        </button>
    `;
}

function toggleDepartment(deptId, categoryId) {
    const index = selectedDepartments.indexOf(deptId);

    if (index > -1) {
        // Deselect
        selectedDepartments.splice(index, 1);
    } else {
        // Check global max limit
        if (selectedDepartments.length >= maxDepartments) {
            return;
        }

        // For categorized departments, check category limit
        if (categoryId !== null) {
            const category = categories.find(c => c.id === categoryId);
            if (category) {
                const categoryDeptIds = category.departments.map(d => d.id);
                const selectedInCategory = selectedDepartments.filter(id => categoryDeptIds.includes(id));
                const maxAllowed = category.maxSelections || 1;

                // If at category limit, remove oldest selection from this category
                if (selectedInCategory.length >= maxAllowed) {
                    // Remove the first selected department from this category
                    const toRemove = selectedInCategory[0];
                    const removeIndex = selectedDepartments.indexOf(toRemove);
                    if (removeIndex > -1) {
                        selectedDepartments.splice(removeIndex, 1);
                    }
                }
            }
        }

        selectedDepartments.push(deptId);
    }

    updateCounter();
    renderDepartments();
    updateSubmitButton();
}

function updateCounter() {
    const counter = document.getElementById('selectionCounter');
    counter.textContent = `${selectedDepartments.length}/${maxDepartments}`;

    if (selectedDepartments.length >= maxDepartments) {
        counter.className = 'px-3 py-1 bg-amber-100 text-amber-700 text-sm font-medium rounded-full';
    } else {
        counter.className = 'px-3 py-1 bg-indigo-100 text-indigo-700 text-sm font-medium rounded-full';
    }
}

function updateSelectionInfo() {
    const infoDiv = document.getElementById('selectionInfo');

    let html = `<p class="mb-2"><strong>You can select up to ${maxDepartments} department(s) in total.</strong></p>`;

    if (categories.length > 0) {
        html += '<ul class="list-disc list-inside space-y-1">';
        categories.forEach(cat => {
            const max = cat.maxSelections || 1;
            html += `<li><strong>${cat.name}:</strong> up to ${max} selection${max > 1 ? 's' : ''}</li>`;
        });
        if (uncategorized.length > 0) {
            html += `<li><strong>Other Departments:</strong> no limit</li>`;
        }
        html += '</ul>';
    }

    infoDiv.innerHTML = html;
}

function updateSubmitButton() {
    const btn = document.getElementById('submitBtn');
    const fullName = document.getElementById('fullName').value.trim();
    const phone = document.getElementById('phone').value.trim();
    const address = document.getElementById('address').value.trim();

    btn.disabled = !fullName || !phone || !address || selectedDepartments.length === 0;
}

// Add input listeners for validation
['fullName', 'phone', 'address'].forEach(id => {
    document.getElementById(id).addEventListener('input', updateSubmitButton);
});

function showError(message) {
    const errorDiv = document.getElementById('errorMessage');
    errorDiv.textContent = message;
    errorDiv.classList.remove('hidden');
}

function hideError() {
    document.getElementById('errorMessage').classList.add('hidden');
}

async function handleSubmit() {
    hideError();

    const fullName = document.getElementById('fullName').value.trim();
    const phone = document.getElementById('phone').value.trim();
    const email = document.getElementById('email').value.trim();
    const address = document.getElementById('address').value.trim();

    // Validate
    if (!fullName || !phone || !address) {
        showError('Please fill in all required fields.');
        return;
    }

    if (selectedDepartments.length === 0) {
        showError('Please select at least one department.');
        return;
    }

    // Disable button
    const btn = document.getElementById('submitBtn');
    btn.disabled = true;
    btn.textContent = 'Submitting...';

    try {
        const response = await fetch('/api/submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                full_name: fullName,
                phone: phone,
                email: email,
                address: address,
                selected_departments: selectedDepartments
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Submission failed');
        }

        // Redirect to thank you page
        window.location.href = '/thank-you';
    } catch (error) {
        console.error('Submission error:', error);
        showError(error.message || 'Failed to submit. Please try again.');
        btn.disabled = false;
        btn.textContent = 'Submit Selection';
    }
}
