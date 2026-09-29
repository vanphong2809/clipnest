// API Render; localhost dùng backend local để tiện phát triển.
window.CLIPNEST_CONFIG = {
  API_BASE_URL: ['localhost', '127.0.0.1'].includes(window.location.hostname)
    ? 'http://localhost:8000'
    : 'https://clipnest-api.onrender.com'
};
