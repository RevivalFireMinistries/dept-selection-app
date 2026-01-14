import { prisma } from '@/lib/db'
import { NextRequest, NextResponse } from 'next/server'
import * as XLSX from 'xlsx'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const type = searchParams.get('type') || 'department'

    if (type === 'department') {
      return exportByDepartment()
    } else if (type === 'member') {
      return exportByMember()
    } else {
      return NextResponse.json({ error: 'Invalid export type' }, { status: 400 })
    }
  } catch (error) {
    console.error('Error exporting:', error)
    return NextResponse.json({ error: 'Export failed' }, { status: 500 })
  }
}

async function exportByDepartment() {
  // Get all departments with their members
  const departments = await prisma.department.findMany({
    include: {
      category: true,
      members: {
        include: {
          member: true
        }
      }
    },
    orderBy: { name: 'asc' }
  })

  const workbook = XLSX.utils.book_new()

  // Create a sheet for each department
  for (const dept of departments) {
    const sheetData = [
      ['Department:', dept.name],
      ['Category:', dept.category?.name || 'Uncategorized'],
      ['Total Members:', dept.members.length.toString()],
      [],
      ['Full Name', 'Phone', 'Email', 'Address', 'Submitted On']
    ]

    dept.members.forEach(m => {
      sheetData.push([
        m.member.fullName,
        m.member.phone,
        m.member.email,
        m.member.address,
        new Date(m.member.createdAt).toLocaleDateString()
      ])
    })

    // Sanitize sheet name (max 31 chars, no special chars)
    const sheetName = dept.name.replace(/[\\/*?[\]:]/g, '').substring(0, 31)
    const worksheet = XLSX.utils.aoa_to_sheet(sheetData)

    // Set column widths
    worksheet['!cols'] = [
      { wch: 25 }, // Name
      { wch: 15 }, // Phone
      { wch: 25 }, // Email
      { wch: 35 }, // Address
      { wch: 12 }  // Date
    ]

    XLSX.utils.book_append_sheet(workbook, worksheet, sheetName)
  }

  // Add summary sheet at the beginning
  const summaryData = [
    ['Department Selection Report - By Department'],
    ['Generated:', new Date().toLocaleString()],
    [],
    ['Department', 'Category', 'Member Count']
  ]

  departments.forEach(dept => {
    summaryData.push([
      dept.name,
      dept.category?.name || 'Uncategorized',
      dept.members.length.toString()
    ])
  })

  const summarySheet = XLSX.utils.aoa_to_sheet(summaryData)
  summarySheet['!cols'] = [{ wch: 30 }, { wch: 20 }, { wch: 15 }]

  // Insert summary at the beginning
  workbook.SheetNames.unshift('Summary')
  workbook.Sheets['Summary'] = summarySheet

  const buffer = XLSX.write(workbook, { type: 'buffer', bookType: 'xlsx' })

  return new NextResponse(buffer, {
    headers: {
      'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'Content-Disposition': `attachment; filename="departments-report-${new Date().toISOString().split('T')[0]}.xlsx"`
    }
  })
}

async function exportByMember() {
  // Get all members with their departments
  const members = await prisma.member.findMany({
    include: {
      departments: {
        include: {
          department: {
            include: {
              category: true
            }
          }
        }
      }
    },
    orderBy: { fullName: 'asc' }
  })

  // Get all departments for column headers
  const allDepartments = await prisma.department.findMany({
    orderBy: { name: 'asc' }
  })

  const workbook = XLSX.utils.book_new()

  // Create header row
  const headerRow = ['Full Name', 'Phone', 'Email', 'Address', 'Submitted On', ...allDepartments.map(d => d.name)]

  const sheetData = [headerRow]

  members.forEach(member => {
    const memberDeptIds = member.departments.map(d => d.departmentId)
    const row = [
      member.fullName,
      member.phone,
      member.email,
      member.address,
      new Date(member.createdAt).toLocaleDateString(),
      ...allDepartments.map(d => memberDeptIds.includes(d.id) ? 'Yes' : '')
    ]
    sheetData.push(row)
  })

  const worksheet = XLSX.utils.aoa_to_sheet(sheetData)

  // Set column widths
  const cols = [
    { wch: 25 }, // Name
    { wch: 15 }, // Phone
    { wch: 25 }, // Email
    { wch: 35 }, // Address
    { wch: 12 }, // Date
    ...allDepartments.map(() => ({ wch: 15 }))
  ]
  worksheet['!cols'] = cols

  XLSX.utils.book_append_sheet(workbook, worksheet, 'Members')

  // Add summary sheet
  const summaryData = [
    ['Department Selection Report - By Member'],
    ['Generated:', new Date().toLocaleString()],
    ['Total Members:', members.length.toString()],
    [],
    ['Department Summary'],
    ['Department', 'Member Count']
  ]

  allDepartments.forEach(dept => {
    const count = members.filter(m =>
      m.departments.some(d => d.departmentId === dept.id)
    ).length
    summaryData.push([dept.name, count.toString()])
  })

  const summarySheet = XLSX.utils.aoa_to_sheet(summaryData)
  summarySheet['!cols'] = [{ wch: 30 }, { wch: 15 }]

  XLSX.utils.book_append_sheet(workbook, summarySheet, 'Summary')

  const buffer = XLSX.write(workbook, { type: 'buffer', bookType: 'xlsx' })

  return new NextResponse(buffer, {
    headers: {
      'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'Content-Disposition': `attachment; filename="members-report-${new Date().toISOString().split('T')[0]}.xlsx"`
    }
  })
}
