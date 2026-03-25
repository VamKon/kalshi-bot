#!/bin/bash
set -e

docker build -t vkon2001/kalshi-trading-backend:latest ./backend
docker push vkon2001/kalshi-trading-backend:latest

docker build -t vkon2001/kalshi-trading-streamlit:latest ./streamlit_app
docker push vkon2001/kalshi-trading-streamlit:latest

echo "All images pushed to Docker Hub successfully."
