import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.registry.registry import load_registry, save_registry


def update_employer(employer_id: str, new_name: str):
    registry = load_registry()
    if employer_id not in registry["employers"]:
        print(f"Employer {employer_id} not found.")
        return
    old_name = registry["employers"][employer_id]["employer_name"]
    registry["employers"][employer_id]["employer_name"] = new_name
    save_registry(registry)
    print(f"✓ Updated {employer_id}: '{old_name}' → '{new_name}'")


def update_plan(plan_id: str, new_plan_name: str = None,
                new_effective_date: str = None):
    registry = load_registry()
    if plan_id not in registry["plans"]:
        print(f"Plan {plan_id} not found.")
        return
    plan = registry["plans"][plan_id]
    if new_plan_name:
        old = plan["plan_name"]
        plan["plan_name"] = new_plan_name
        print(f"✓ Updated {plan_id} name: '{old}' → '{new_plan_name}'")
    if new_effective_date:
        plan["effective_date"] = new_effective_date
        print(f"✓ Updated {plan_id} effective date: {new_effective_date}")
    save_registry(registry)


if __name__ == "__main__":
    print("Updating registry with correct names...\n")

    # Fix employers
    update_employer("EMP_001", "Capital One Financial Corporation")
    update_employer("EMP_002", "Prudential Financial")

    # Fix plans
    update_plan("PLAN_001",
                new_plan_name="Capital One Associate Savings Plan",
                new_effective_date="2024-01-01")
    update_plan("PLAN_002",
                new_plan_name="Prudential Employee Savings Plan (PESP)",
                new_effective_date="2024-03-01")

    # Verify
    print("\nVerifying...")
    registry = load_registry()
    for eid, emp in registry["employers"].items():
        print(f"  {eid}: {emp['employer_name']}")
    for pid, plan in registry["plans"].items():
        print(f"  {pid}: {plan['plan_name']} | {plan['effective_date']}")