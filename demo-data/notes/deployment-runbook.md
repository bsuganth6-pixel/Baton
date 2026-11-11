---
title: Production deployment runbook
author: Arjun Mehta
date: 2026-07-08
---

# Production deployment runbook

## Deploying

Merging to main starts the deploy workflow in GitHub Actions. The workflow connects to the production droplet over SSH, pulls main and restarts campus-api and campus-notify with pm2.

## Configuration

The API reads its settings from /etc/campus/.env on the droplet. The database line looks like this:

DATABASE_URL=postgresql://campus_admin:Zx9-demo-NotReal-41@db-campus.example.internal:25060/campus?sslmode=require

## Backups

Backups: automated daily snapshot at 02:00 IST, retained for 7 days.

## TLS certificates

Certificates come from Let's Encrypt through certbot. They renew automatically every 60 days.

## Domain

The domain campusconnect.example expires on 2026-11-28. Domain registration renewals: Owner: TBD. The registrar account was opened by Kabir Shah.
