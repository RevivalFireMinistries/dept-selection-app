'use client'

import { useState, useEffect } from 'react'

export default function SettingsPage() {
  const [settings, setSettings] = useState({
    maxDepartments: '3',
    adminPassword: ''
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    fetchSettings()
  }, [])

  const fetchSettings = async () => {
    try {
      const res = await fetch('/api/settings')
      const data = await res.json()
      setSettings({
        maxDepartments: data.maxDepartments || '3',
        adminPassword: data.adminPassword || ''
      })
    } catch (error) {
      console.error('Error fetching settings:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async (key: string, value: string) => {
    setSaving(true)
    setMessage('')

    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value })
      })

      if (res.ok) {
        setMessage('Settings saved successfully!')
        setTimeout(() => setMessage(''), 3000)
      }
    } catch (error) {
      setMessage('Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="text-center py-8">Loading...</div>
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Settings</h1>

      {message && (
        <div className={`p-4 rounded-lg ${message.includes('success') ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {message}
        </div>
      )}

      <div className="bg-white rounded-lg shadow divide-y">
        {/* Max Departments */}
        <div className="p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-2">
            Maximum Departments Per Member
          </h2>
          <p className="text-sm text-gray-600 mb-4">
            Set the maximum number of departments a member can select.
          </p>
          <div className="flex items-center gap-4">
            <input
              type="number"
              min="1"
              max="20"
              value={settings.maxDepartments}
              onChange={(e) => setSettings({ ...settings, maxDepartments: e.target.value })}
              className="w-24 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              onClick={() => handleSave('maxDepartments', settings.maxDepartments)}
              disabled={saving}
              className="bg-blue-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-blue-700 disabled:bg-blue-400 transition-colors"
            >
              Save
            </button>
          </div>
        </div>

        {/* Admin Password */}
        <div className="p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-2">
            Admin Password
          </h2>
          <p className="text-sm text-gray-600 mb-4">
            Change the password required to access the admin panel.
          </p>
          <div className="flex items-center gap-4">
            <input
              type="password"
              value={settings.adminPassword}
              onChange={(e) => setSettings({ ...settings, adminPassword: e.target.value })}
              className="w-64 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Enter new password"
            />
            <button
              onClick={() => handleSave('adminPassword', settings.adminPassword)}
              disabled={saving}
              className="bg-blue-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-blue-700 disabled:bg-blue-400 transition-colors"
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
