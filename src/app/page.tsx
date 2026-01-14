'use client'

import { useState, useEffect } from 'react'

interface Department {
  id: number
  name: string
  categoryId: number | null
}

interface Category {
  id: number
  name: string
  departments: Department[]
}

interface FormData {
  fullName: string
  phone: string
  email: string
  address: string
  selectedDepartments: number[]
}

export default function Home() {
  const [categories, setCategories] = useState<Category[]>([])
  const [uncategorized, setUncategorized] = useState<Department[]>([])
  const [maxDepartments, setMaxDepartments] = useState(3)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const [formData, setFormData] = useState<FormData>({
    fullName: '',
    phone: '',
    email: '',
    address: '',
    selectedDepartments: []
  })

  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      const [deptRes, settingsRes] = await Promise.all([
        fetch('/api/departments'),
        fetch('/api/settings')
      ])

      const deptData = await deptRes.json()
      const settingsData = await settingsRes.json()

      setCategories(deptData.categories || [])
      setUncategorized(deptData.uncategorized || [])
      setMaxDepartments(parseInt(settingsData.maxDepartments) || 3)
    } catch (err) {
      setError('Failed to load departments')
    } finally {
      setLoading(false)
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target
    setFormData(prev => ({ ...prev, [name]: value }))
  }

  const handleDepartmentToggle = (deptId: number, categoryId: number | null) => {
    setFormData(prev => {
      let newSelected = [...prev.selectedDepartments]

      if (newSelected.includes(deptId)) {
        newSelected = newSelected.filter(id => id !== deptId)
      } else {
        if (categoryId !== null) {
          const category = categories.find(c => c.id === categoryId)
          if (category) {
            const categoryDeptIds = category.departments.map(d => d.id)
            newSelected = newSelected.filter(id => !categoryDeptIds.includes(id))
          }
        }

        if (newSelected.length >= maxDepartments) {
          return prev
        }

        newSelected.push(deptId)
      }

      return { ...prev, selectedDepartments: newSelected }
    })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!formData.fullName || !formData.phone || !formData.address) {
      setError('Please fill in all required fields')
      return
    }

    if (formData.selectedDepartments.length === 0) {
      setError('Please select at least one department')
      return
    }

    setSubmitting(true)

    try {
      const res = await fetch('/api/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      })

      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || 'Submission failed')
      }

      window.location.href = '/thank-you'
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submission failed')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-indigo-100 via-purple-50 to-pink-100 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin"></div>
          <p className="text-gray-600 font-medium">Loading...</p>
        </div>
      </div>
    )
  }

  const selectedCount = formData.selectedDepartments.length

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-100 via-purple-50 to-pink-100">
      {/* Header */}
      <div className="bg-white/80 backdrop-blur-sm shadow-sm sticky top-0 z-10">
        <div className="max-w-lg mx-auto px-4 py-4">
          <h1 className="text-xl font-bold bg-gradient-to-r from-indigo-600 to-purple-600 bg-clip-text text-transparent">
            Department Selection
          </h1>
        </div>
      </div>

      <div className="max-w-lg mx-auto px-4 py-6 pb-32">
        {/* Welcome Card */}
        <div className="bg-gradient-to-r from-indigo-600 to-purple-600 rounded-2xl p-6 text-white mb-6 shadow-lg">
          <h2 className="text-2xl font-bold mb-2">Welcome!</h2>
          <p className="text-indigo-100">
            Choose the departments you would like to serve in this year. Your service makes a difference!
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Personal Information */}
          <div className="bg-white rounded-2xl shadow-lg shadow-gray-200/50 p-6">
            <div className="flex items-center gap-3 mb-5">
              <div className="w-10 h-10 bg-indigo-100 rounded-xl flex items-center justify-center">
                <svg className="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </div>
              <h2 className="text-lg font-semibold text-gray-900">Personal Information</h2>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Full Name <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  name="fullName"
                  value={formData.fullName}
                  onChange={handleInputChange}
                  className="w-full px-4 py-3 bg-gray-50 border-0 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-all"
                  placeholder="Enter your full name"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Phone Number <span className="text-red-500">*</span>
                </label>
                <input
                  type="tel"
                  name="phone"
                  value={formData.phone}
                  onChange={handleInputChange}
                  className="w-full px-4 py-3 bg-gray-50 border-0 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-all"
                  placeholder="+263 77 123 4567"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Email <span className="text-gray-400 text-xs font-normal">(optional)</span>
                </label>
                <input
                  type="email"
                  name="email"
                  value={formData.email}
                  onChange={handleInputChange}
                  className="w-full px-4 py-3 bg-gray-50 border-0 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-all"
                  placeholder="your@email.com"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Address <span className="text-red-500">*</span>
                </label>
                <textarea
                  name="address"
                  value={formData.address}
                  onChange={handleInputChange}
                  rows={2}
                  className="w-full px-4 py-3 bg-gray-50 border-0 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-all resize-none"
                  placeholder="Enter your address"
                />
              </div>
            </div>
          </div>

          {/* Department Selection */}
          <div className="bg-white rounded-2xl shadow-lg shadow-gray-200/50 p-6">
            <div className="flex items-center justify-between mb-5">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-purple-100 rounded-xl flex items-center justify-center">
                  <svg className="w-5 h-5 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                  </svg>
                </div>
                <h2 className="text-lg font-semibold text-gray-900">Select Departments</h2>
              </div>
              <div className={`px-3 py-1 rounded-full text-sm font-medium ${
                selectedCount >= maxDepartments
                  ? 'bg-amber-100 text-amber-700'
                  : 'bg-indigo-100 text-indigo-700'
              }`}>
                {selectedCount}/{maxDepartments}
              </div>
            </div>

            <p className="text-sm text-gray-500 mb-6 bg-gray-50 rounded-xl p-3">
              Select up to <span className="font-semibold text-gray-700">{maxDepartments}</span> department(s).
              For grouped categories, you can only choose one.
            </p>

            {/* Categorized Departments */}
            {categories.map(category => (
              <div key={category.id} className="mb-6">
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-sm font-semibold text-gray-800">{category.name}</span>
                  <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">pick one</span>
                </div>
                <div className="grid gap-2">
                  {category.departments.map(dept => {
                    const isSelected = formData.selectedDepartments.includes(dept.id)
                    const isDisabled = !isSelected && selectedCount >= maxDepartments

                    return (
                      <button
                        key={dept.id}
                        type="button"
                        disabled={isDisabled && !isSelected}
                        onClick={() => handleDepartmentToggle(dept.id, category.id)}
                        className={`w-full p-4 rounded-xl text-left transition-all duration-200 ${
                          isSelected
                            ? 'bg-gradient-to-r from-indigo-500 to-purple-500 text-white shadow-lg shadow-indigo-200 scale-[1.02]'
                            : isDisabled
                              ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                              : 'bg-gray-50 text-gray-700 hover:bg-gray-100 active:scale-[0.98]'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium">{dept.name}</span>
                          {isSelected && (
                            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                              <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                            </svg>
                          )}
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}

            {/* Uncategorized Departments */}
            {uncategorized.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-sm font-semibold text-gray-800">Other Departments</span>
                  <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">pick any</span>
                </div>
                <div className="grid gap-2">
                  {uncategorized.map(dept => {
                    const isSelected = formData.selectedDepartments.includes(dept.id)
                    const isDisabled = !isSelected && selectedCount >= maxDepartments

                    return (
                      <button
                        key={dept.id}
                        type="button"
                        disabled={isDisabled}
                        onClick={() => handleDepartmentToggle(dept.id, null)}
                        className={`w-full p-4 rounded-xl text-left transition-all duration-200 ${
                          isSelected
                            ? 'bg-gradient-to-r from-indigo-500 to-purple-500 text-white shadow-lg shadow-indigo-200 scale-[1.02]'
                            : isDisabled
                              ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                              : 'bg-gray-50 text-gray-700 hover:bg-gray-100 active:scale-[0.98]'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium">{dept.name}</span>
                          {isSelected && (
                            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                              <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                            </svg>
                          )}
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Error Message */}
          {error && (
            <div className="bg-red-50 text-red-600 p-4 rounded-xl flex items-center gap-3">
              <svg className="w-5 h-5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
              </svg>
              <span>{error}</span>
            </div>
          )}
        </form>
      </div>

      {/* Fixed Submit Button */}
      <div className="fixed bottom-0 left-0 right-0 bg-white/80 backdrop-blur-lg border-t border-gray-200 p-4">
        <div className="max-w-lg mx-auto">
          <button
            onClick={handleSubmit}
            disabled={submitting || selectedCount === 0}
            className={`w-full py-4 px-6 rounded-xl font-semibold text-white transition-all duration-200 ${
              submitting || selectedCount === 0
                ? 'bg-gray-300 cursor-not-allowed'
                : 'bg-gradient-to-r from-indigo-600 to-purple-600 hover:shadow-lg hover:shadow-indigo-300 active:scale-[0.98]'
            }`}
          >
            {submitting ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Submitting...
              </span>
            ) : (
              `Submit Selection${selectedCount > 0 ? ` (${selectedCount})` : ''}`
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
