#!/bin/bash
# Build and push both Docker images, then restart the running pods.
# Always uses --no-cache to prevent Docker layer caching from serving stale code.
set -e

docker build --no-cache -t vkon2001/kalshi-trading-backend:latest ./backend
docker push vkon2001/kalshi-trading-backend:latest

docker build --no-cache -t vkon2001/kalshi-trading-streamlit:latest ./streamlit_app
docker push vkon2001/kalshi-trading-streamlit:latest

echo "All images pushed to Docker Hub successfully."

# Restart running pods so they pull the new :latest images
kubectl rollout restart deployment/kalshi-backend deployment/kalshi-streamlit -n trading
