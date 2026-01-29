#!/bin/bash
set -e

echo "=== SalesClutch Installation Script ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo ./install.sh)"
    exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
apt update
apt install -y python3.11 python3.11-venv nginx certbot python3-certbot-nginx

# Create application directory
echo "Setting up application directory..."
mkdir -p /opt/salesclutch
cd /opt/salesclutch

# Copy application files (assuming current directory has the files)
echo "Copying application files..."
cp -r . /opt/salesclutch/

# Create Python virtual environment
echo "Setting up Python environment..."
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create necessary directories
mkdir -p uploads data

# Set permissions
chown -R www-data:www-data /opt/salesclutch
chmod 755 /opt/salesclutch

# Set up environment file
if [ ! -f /opt/salesclutch/.env ]; then
    echo "Creating .env file..."
    cp .env.example .env
    echo ""
    echo "!!! IMPORTANT: Edit /opt/salesclutch/.env and add your OpenAI API key !!!"
    echo ""
fi

# Install systemd service
echo "Installing systemd service..."
cp deploy/salesclutch.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable salesclutch

# Install nginx configuration
echo "Configuring nginx..."
cp deploy/nginx.conf /etc/nginx/sites-available/salesclutch
ln -sf /etc/nginx/sites-available/salesclutch /etc/nginx/sites-enabled/

# Test nginx configuration
nginx -t

# Get SSL certificate
echo ""
echo "Getting SSL certificate..."
certbot --nginx -d salesclutch.membies.com --non-interactive --agree-tos --email admin@membies.com || {
    echo "SSL certificate setup failed. You may need to run certbot manually."
}

# Start services
echo "Starting services..."
systemctl start salesclutch
systemctl reload nginx

echo ""
echo "=== Installation Complete ==="
echo ""
echo "SalesClutch is now running at https://salesclutch.membies.com"
echo ""
echo "Commands:"
echo "  sudo systemctl status salesclutch  - Check status"
echo "  sudo systemctl restart salesclutch - Restart app"
echo "  sudo journalctl -u salesclutch -f  - View logs"
echo ""
echo "Don't forget to add your OpenAI API key to /opt/salesclutch/.env"
