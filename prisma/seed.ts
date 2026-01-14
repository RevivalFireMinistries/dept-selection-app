import { PrismaClient } from '@prisma/client'

const prisma = new PrismaClient()

async function main() {
  // Clear existing data
  await prisma.memberDepartment.deleteMany()
  await prisma.member.deleteMany()
  await prisma.department.deleteMany()
  await prisma.category.deleteMany()
  await prisma.settings.deleteMany()

  // Create settings
  await prisma.settings.create({
    data: { key: 'maxDepartments', value: '3' }
  })
  await prisma.settings.create({
    data: { key: 'adminPassword', value: 'admin123' }
  })

  // Create categories with departments
  const musicMinistry = await prisma.category.create({
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

  const childrensMinistry = await prisma.category.create({
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

  const outreach = await prisma.category.create({
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

  console.log('Database seeded successfully!')
}

main()
  .catch((e) => {
    console.error(e)
    process.exit(1)
  })
  .finally(async () => {
    await prisma.$disconnect()
  })
