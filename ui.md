# UI Design System - Jinja2 + Tailwind CSS

This document provides comprehensive instructions for building consistent, modern UIs using Jinja2 templates with Tailwind CSS. Follow these patterns exactly to maintain design consistency.

## Design Philosophy

- **Clean & Modern**: Soft, card-based design with generous whitespace
- **Mobile-First**: All layouts must be responsive
- **Muted Colors**: Use softer, less saturated color variants
- **Accessible**: Clear contrast, focus states, and readable typography
- **No Component Libraries**: Pure Tailwind CSS utilities only

---

## Color Palette

### Primary Colors (Blue - Muted)
```
Primary:        blue-600    (buttons, links, active states)
Primary Hover:  blue-700    (button hover)
Primary Light:  blue-50     (backgrounds, highlights)
Primary Muted:  blue-100    (badges, subtle backgrounds)
Primary Text:   blue-800    (text on light blue backgrounds)
```

### Semantic Colors (Muted Versions)
```
Success:        emerald-600  (not green - softer)
Success Light:  emerald-50
Success Text:   emerald-800

Warning:        amber-500    (not yellow - softer)
Warning Light:  amber-50
Warning Text:   amber-800

Error:          rose-600     (not red - softer)
Error Light:    rose-50
Error Text:     rose-800

Info:           sky-500
Info Light:     sky-50
Info Text:      sky-800
```

### Neutral Colors
```
Background:     slate-50     (page background)
Card:           white
Border:         slate-200
Text Primary:   slate-800    (headings)
Text Body:      slate-600    (body text)
Text Muted:     slate-400    (placeholder, hints)
Text Disabled:  slate-300
```

---

## Layout Patterns

### Page Structure
```html
<div class="min-h-screen bg-slate-50">
    <div class="max-w-4xl mx-auto px-4 py-6">
        <!-- Page content here -->
    </div>
</div>
```

### Wide Page (Dashboard/Admin)
```html
<div class="min-h-screen bg-slate-50">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <!-- Wide content here -->
    </div>
</div>
```

### Section Spacing
```html
<div class="space-y-6">
    <!-- Sections with consistent vertical gaps -->
</div>
```

---

## Card Components

### Basic Card
```html
<div class="bg-white rounded-xl shadow-sm p-6">
    <!-- Card content -->
</div>
```

### Card with Header
```html
<div class="bg-white rounded-xl shadow-sm overflow-hidden">
    <div class="px-6 py-4 border-b border-slate-100">
        <h3 class="text-lg font-semibold text-slate-800">Card Title</h3>
        <p class="text-sm text-slate-500">Optional description</p>
    </div>
    <div class="p-6">
        <!-- Card body -->
    </div>
</div>
```

### Interactive Card (Clickable)
```html
<div class="bg-white rounded-xl shadow-sm p-6 hover:shadow-md transition-shadow cursor-pointer">
    <!-- Clickable card content -->
</div>
```

### Card with Colored Left Border
```html
<div class="bg-white rounded-xl shadow-sm p-5 border-l-4 border-blue-500">
    <!-- Accented card content -->
</div>
```

### Stats Card
```html
<div class="bg-white rounded-xl shadow-sm p-4">
    <p class="text-sm text-slate-500">Stat Label</p>
    <p class="text-2xl font-bold text-slate-800">123</p>
</div>
```

---

## Typography

### Headings
```html
<h1 class="text-2xl font-bold text-slate-800">Page Title</h1>
<h2 class="text-xl font-semibold text-slate-800">Section Title</h2>
<h3 class="text-lg font-semibold text-slate-800">Card Title</h3>
<h4 class="text-base font-medium text-slate-700">Subsection</h4>
```

### Body Text
```html
<p class="text-slate-600">Regular body text</p>
<p class="text-sm text-slate-500">Secondary/smaller text</p>
<p class="text-xs text-slate-400">Hint text or metadata</p>
```

### Links
```html
<a href="#" class="text-blue-600 hover:text-blue-700 hover:underline">Link text</a>
```

---

## Buttons

### Primary Button
```html
<button class="px-4 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors">
    Primary Action
</button>
```

### Secondary Button
```html
<button class="px-4 py-2 bg-slate-100 text-slate-700 font-medium rounded-lg hover:bg-slate-200 transition-colors">
    Secondary Action
</button>
```

### Outline Button
```html
<button class="px-4 py-2 border border-slate-300 text-slate-700 font-medium rounded-lg hover:bg-slate-50 transition-colors">
    Outline Action
</button>
```

### Danger Button
```html
<button class="px-4 py-2 bg-rose-600 text-white font-medium rounded-lg hover:bg-rose-700 transition-colors">
    Delete
</button>
```

### Soft Danger Button
```html
<button class="px-4 py-2 bg-rose-50 text-rose-700 font-medium rounded-lg hover:bg-rose-100 transition-colors">
    Remove
</button>
```

### Small Button
```html
<button class="px-3 py-1.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700">
    Small
</button>
```

### Button with Icon
```html
<button class="px-4 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 flex items-center gap-2">
    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path>
    </svg>
    Add Item
</button>
```

### Disabled Button
```html
<button disabled class="px-4 py-2 bg-slate-200 text-slate-400 font-medium rounded-lg cursor-not-allowed">
    Disabled
</button>
```

---

## Form Elements

### Text Input
```html
<div>
    <label class="block text-sm font-medium text-slate-700 mb-1">Label</label>
    <input type="text" placeholder="Placeholder..."
           class="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-shadow">
</div>
```

### Select Dropdown
```html
<div>
    <label class="block text-sm font-medium text-slate-700 mb-1">Label</label>
    <select class="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none bg-white">
        <option value="">-- Select option --</option>
        <option value="1">Option 1</option>
    </select>
</div>
```

### Textarea
```html
<div>
    <label class="block text-sm font-medium text-slate-700 mb-1">Label</label>
    <textarea rows="3" placeholder="Enter description..."
              class="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none resize-none"></textarea>
</div>
```

### Checkbox
```html
<label class="flex items-center gap-2 cursor-pointer">
    <input type="checkbox" class="w-4 h-4 text-blue-600 rounded focus:ring-blue-500 border-slate-300">
    <span class="text-sm text-slate-700">Checkbox label</span>
</label>
```

### Radio Buttons
```html
<div class="space-y-2">
    <label class="flex items-center gap-2 cursor-pointer">
        <input type="radio" name="group" class="w-4 h-4 text-blue-600 focus:ring-blue-500 border-slate-300">
        <span class="text-sm text-slate-700">Option 1</span>
    </label>
    <label class="flex items-center gap-2 cursor-pointer">
        <input type="radio" name="group" class="w-4 h-4 text-blue-600 focus:ring-blue-500 border-slate-300">
        <span class="text-sm text-slate-700">Option 2</span>
    </label>
</div>
```

### Input with Error
```html
<div>
    <label class="block text-sm font-medium text-slate-700 mb-1">Email</label>
    <input type="email"
           class="w-full px-3 py-2 border border-rose-300 rounded-lg focus:ring-2 focus:ring-rose-500 focus:border-rose-500 outline-none bg-rose-50">
    <p class="text-sm text-rose-600 mt-1">Please enter a valid email address</p>
</div>
```

### Form Grid Layout
```html
<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    <div><!-- Input 1 --></div>
    <div><!-- Input 2 --></div>
</div>
```

---

## Badges & Tags

### Status Badges
```html
<!-- Default/Info -->
<span class="text-xs bg-blue-100 text-blue-800 px-2 py-0.5 rounded-full font-medium">Default</span>

<!-- Success -->
<span class="text-xs bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-full font-medium">Approved</span>

<!-- Warning -->
<span class="text-xs bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full font-medium">Pending</span>

<!-- Error -->
<span class="text-xs bg-rose-100 text-rose-800 px-2 py-0.5 rounded-full font-medium">Rejected</span>

<!-- Neutral -->
<span class="text-xs bg-slate-100 text-slate-700 px-2 py-0.5 rounded-full font-medium">Draft</span>
```

### Pill Badges (Rectangular)
```html
<span class="text-xs bg-blue-100 text-blue-800 px-2 py-0.5 rounded font-medium">Tag</span>
```

---

## Modals

### Modal Container
```html
<div id="modal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50">
    <div class="bg-white rounded-xl shadow-xl max-w-md w-full mx-4 p-6">
        <h3 class="text-lg font-semibold text-slate-800 mb-4">Modal Title</h3>

        <!-- Modal content -->

        <div class="flex justify-end gap-3 mt-6">
            <button onclick="closeModal()" class="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200">
                Cancel
            </button>
            <button class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                Confirm
            </button>
        </div>
    </div>
</div>
```

### Modal JavaScript Pattern
```javascript
function openModal() {
    document.getElementById('modal').classList.remove('hidden');
    document.getElementById('modal').classList.add('flex');
}

function closeModal() {
    document.getElementById('modal').classList.add('hidden');
    document.getElementById('modal').classList.remove('flex');
}
```

### Confirmation Modal (Danger)
```html
<div class="bg-white rounded-xl shadow-xl max-w-sm w-full mx-4 p-6 text-center">
    <div class="w-16 h-16 bg-rose-100 rounded-full flex items-center justify-center mx-auto mb-4">
        <svg class="w-8 h-8 text-rose-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
        </svg>
    </div>
    <h3 class="text-xl font-bold text-slate-800 mb-2">Delete Item?</h3>
    <p class="text-slate-600 mb-6">This action cannot be undone.</p>
    <div class="flex gap-3">
        <button class="flex-1 py-2 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200">Cancel</button>
        <button class="flex-1 py-2 bg-rose-600 text-white rounded-lg hover:bg-rose-700">Delete</button>
    </div>
</div>
```

---

## Tables

### Basic Table
```html
<div class="bg-white rounded-xl shadow-sm overflow-hidden">
    <table class="min-w-full divide-y divide-slate-200">
        <thead class="bg-slate-50">
            <tr>
                <th class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Name</th>
                <th class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                <th class="px-6 py-3 text-right text-xs font-medium text-slate-500 uppercase tracking-wider">Actions</th>
            </tr>
        </thead>
        <tbody class="bg-white divide-y divide-slate-100">
            <tr class="hover:bg-slate-50">
                <td class="px-6 py-4 whitespace-nowrap text-sm text-slate-800">Item Name</td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="text-xs bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-full">Active</span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-right text-sm">
                    <button class="text-blue-600 hover:text-blue-800">Edit</button>
                </td>
            </tr>
        </tbody>
    </table>
</div>
```

---

## Lists

### Simple List
```html
<div class="bg-white rounded-xl shadow-sm divide-y divide-slate-100">
    <div class="p-4 hover:bg-slate-50">
        <h4 class="font-medium text-slate-800">List Item Title</h4>
        <p class="text-sm text-slate-500">Description or metadata</p>
    </div>
    <div class="p-4 hover:bg-slate-50">
        <h4 class="font-medium text-slate-800">Another Item</h4>
        <p class="text-sm text-slate-500">More details here</p>
    </div>
</div>
```

### List with Actions
```html
<div class="p-4 flex items-center justify-between hover:bg-slate-50">
    <div>
        <h4 class="font-medium text-slate-800">Item Title</h4>
        <p class="text-sm text-slate-500">Subtitle</p>
    </div>
    <button class="text-rose-600 hover:text-rose-800">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
        </svg>
    </button>
</div>
```

---

## Alerts & Banners

### Info Alert
```html
<div class="bg-sky-50 border border-sky-200 rounded-xl p-4 flex items-start gap-3">
    <svg class="w-5 h-5 text-sky-600 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
    </svg>
    <p class="text-sky-800">This is an informational message.</p>
</div>
```

### Success Alert
```html
<div class="bg-emerald-50 border border-emerald-200 rounded-xl p-4 flex items-start gap-3">
    <svg class="w-5 h-5 text-emerald-600 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
    </svg>
    <p class="text-emerald-800">Operation completed successfully!</p>
</div>
```

### Warning Alert
```html
<div class="bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-start gap-3">
    <svg class="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
    </svg>
    <p class="text-amber-800">Please review before proceeding.</p>
</div>
```

### Error Alert
```html
<div class="bg-rose-50 border border-rose-200 rounded-xl p-4 flex items-start gap-3">
    <svg class="w-5 h-5 text-rose-600 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
    </svg>
    <p class="text-rose-800">An error occurred. Please try again.</p>
</div>
```

---

## Icons

Use **Heroicons** (https://heroicons.com) - outline style, 24x24 size.

### Standard Icon Size
```html
<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <!-- path here -->
</svg>
```

### Icon in Colored Circle
```html
<div class="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
    <svg class="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <!-- path here -->
    </svg>
</div>
```

### Common Icons (Heroicons Outline)
```html
<!-- Plus -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path>

<!-- Check -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>

<!-- X / Close -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>

<!-- Trash -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>

<!-- Edit / Pencil -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path>

<!-- Search -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>

<!-- Calendar -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path>

<!-- User -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"></path>

<!-- Chevron Down -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>

<!-- Chevron Right -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path>

<!-- Arrow Left (Back) -->
<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path>
```

---

## Loading States

### Spinner
```html
<div class="flex items-center justify-center p-8">
    <svg class="animate-spin h-8 w-8 text-blue-600" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
    </svg>
</div>
```

### Loading Text
```html
<p class="text-slate-400 text-sm text-center py-4">Loading...</p>
```

### Skeleton Loader
```html
<div class="animate-pulse">
    <div class="h-4 bg-slate-200 rounded w-3/4 mb-2"></div>
    <div class="h-4 bg-slate-200 rounded w-1/2"></div>
</div>
```

---

## Empty States

```html
<div class="text-center py-12">
    <svg class="w-16 h-16 text-slate-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"></path>
    </svg>
    <h3 class="text-lg font-medium text-slate-800 mb-1">No items yet</h3>
    <p class="text-slate-500 mb-4">Get started by creating your first item.</p>
    <button class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
        Create Item
    </button>
</div>
```

---

## Navigation

### Simple Header
```html
<header class="bg-white shadow-sm">
    <div class="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
        <h1 class="text-xl font-bold text-slate-800">App Name</h1>
        <nav class="flex items-center gap-4">
            <a href="#" class="text-slate-600 hover:text-blue-600">Link</a>
            <button class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">Action</button>
        </nav>
    </div>
</header>
```

### Tab Navigation
```html
<div class="border-b border-slate-200">
    <nav class="flex gap-4">
        <button class="px-4 py-3 text-blue-600 border-b-2 border-blue-600 font-medium">Active Tab</button>
        <button class="px-4 py-3 text-slate-500 hover:text-slate-700">Inactive Tab</button>
        <button class="px-4 py-3 text-slate-500 hover:text-slate-700">Another Tab</button>
    </nav>
</div>
```

---

## Responsive Patterns

### Hide/Show by Breakpoint
```html
<div class="hidden md:block">Desktop only</div>
<div class="md:hidden">Mobile only</div>
```

### Responsive Grid
```html
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
    <!-- Items -->
</div>
```

### Responsive Flex
```html
<div class="flex flex-col md:flex-row gap-4">
    <!-- Items stack on mobile, row on desktop -->
</div>
```

---

## Quick Reference - Common Patterns

```
Rounded corners:     rounded-lg (medium), rounded-xl (large)
Shadows:             shadow-sm (subtle), shadow (default), shadow-md (pronounced)
Spacing:             p-4 (compact), p-6 (standard), gap-4 (between items)
Transitions:         transition-colors, transition-shadow, transition-all
Focus ring:          focus:ring-2 focus:ring-blue-500
Border:              border border-slate-200
Divider:             divide-y divide-slate-100
Hover background:    hover:bg-slate-50
```

---

## Summary for AI Agents

When building UIs with this system:

1. **Always use muted colors** - slate for neutrals, blue for primary, emerald/amber/rose for semantic
2. **Cards are the primary container** - `bg-white rounded-xl shadow-sm`
3. **Generous spacing** - `space-y-6` between sections, `p-6` inside cards
4. **Consistent focus states** - `focus:ring-2 focus:ring-blue-500`
5. **Mobile-first** - Start with single column, expand with `md:` and `lg:` breakpoints
6. **Heroicons only** - Outline style, w-5 h-5 standard size
7. **Subtle interactions** - `hover:bg-slate-50`, `hover:shadow-md`, `transition-*`
8. **No component libraries** - Pure Tailwind utilities only
