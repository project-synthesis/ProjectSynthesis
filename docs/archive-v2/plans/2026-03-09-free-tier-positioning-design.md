# Free Tier Positioning Design

**Date:** 2026-03-09
**Status:** Approved
**Scope:** GitHub Marketplace listing, messaging and positioning, usage policy

---

## Decision

Project Synthesis is free for everyone — personal accounts and organizations — with no paid tier, no seat limits, and no feature gates. The Apache 2.0 license governs all use. A short usage policy makes the community nature explicit and sets correct expectations around support.

---

## GitHub Marketplace

- Available to: personal accounts **and** organizations
- Pricing: single free plan, no tiers
- No install restrictions by account type

---

## Positioning & Messaging

Primary audience in all docs, copy, and the Marketplace description: **"AI engineers and development teams."**

Neither over-indexed toward individual hobbyists nor toward enterprise buyers. The self-hosted model is the natural leveler — every user, whether a solo developer or a team, runs their own instance and owns their own data. That parity is a feature, not a limitation, and the messaging should reflect it.

---

## Usage Policy (TERMS.md)

Three points, stated plainly:

1. **Free for everyone.** Personal use, team use, organizational use — no conditions, no registration, no license key.
2. **No SLA or vendor relationship.** There is no guaranteed response time, no support contract, and no enterprise tier. Issues and PRs are handled by the community on a best-effort basis.
3. **Contribution is the sustainability model.** Users who build on Project Synthesis are encouraged — not required — to contribute back via pull requests, bug reports, documentation improvements, or community support.

---

## What This Is Not

- Not freemium. There is no paid upgrade path implied or planned.
- Not a "community edition" implying a commercial edition exists elsewhere.
- Not a support product. Zen Resources is the initiating organization, not a vendor.

---

## Implementation

1. Create `TERMS.md` at the project root with the usage policy
2. Reference `TERMS.md` from `README.md` and `SUPPORT.md`
3. Update GitHub Marketplace listing to enable organizational installs
4. Ensure `VISION.md` open source section is consistent with this policy (already aligned)
