import { prisma } from '@/lib/db'
import { NextResponse } from 'next/server'

export async function POST() {
  try {
    // Check if already seeded
    const existingSettings = await prisma.settings.findFirst()
    if (existingSettings) {
      return NextResponse.json({ message: 'Database already seeded' })
    }

    // Create settings
    await prisma.settings.create({
      data: { key: 'maxDepartments', value: '3' }
    })
    await prisma.settings.create({
      data: { key: 'adminPassword', value: 'admin123' }
    })

    // Create categories with departments
    await prisma.category.create({
      data: {
        name: 'Music Ministry',
        departments: {
          create: [
            { name: 'Choir' },
            { name: 'Praise Team' },
            { name: 'Sound & Media' },
          ]
        }
      }
    })

    await prisma.category.create({
      data: {
        name: "Children's Ministry",
        departments: {
          create: [
            { name: 'Sunday School Teachers' },
            { name: 'Nursery' },
          ]
        }
      }
    })

    await prisma.category.create({
      data: {
        name: 'Outreach',
        departments: {
          create: [
            { name: 'Evangelism Team' },
            { name: 'Community Service' },
          ]
        }
      }
    })

    // Create uncategorized departments
    await prisma.department.createMany({
      data: [
        { name: 'Ushering' },
        { name: 'Prayer Team' },
        { name: 'Hospitality' },
      ]
    })

    return NextResponse.json({ message: 'Database seeded successfully!' })
  } catch (error) {
    console.error('Seed error:', error)
    return NextResponse.json({ error: 'Failed to seed database' }, { status: 500 })
  }
}
