---
title: CampusConnect architecture overview
author: Arjun Mehta
date: 2026-06-02
---

# CampusConnect architecture overview

CampusConnect is the student-run events and clubs app for the college. It has three parts: the mobile app, the API and the notification worker.

## Mobile app

The app is built with React Native and is led by Rohan Desai. Releases go to the Play Store only. There is no iOS build yet.

## API (campus-api)

- Runtime: Node 18 (LTS) with Express.
- Database: PostgreSQL 15 on a managed instance.
- The connection pool size is 20 because the managed plan allows at most 25 connections.
- The public API is rate limited to 100 requests per minute per user.
- Event posts are approved by the faculty coordinator, Meera Iyer, before they go live.

## Notification worker (campus-notify)

campus-notify sends push notifications and the weekly digest email. It runs under pm2 on the same droplet as the API.

## Hosting

Production runs on a single DigitalOcean droplet named campus-prod-1 with Nginx in front. Event posters are stored in a Spaces bucket called campus-posters.
