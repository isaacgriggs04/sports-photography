import React, { useEffect, useRef, useState } from 'react';
import { Search, ChevronRight, Calendar, Camera, ArrowLeft, Trophy, X, Users, Upload, LogIn, LogOut, ShoppingCart, CheckCircle, Download, User, BarChart3, Settings, Instagram, Bell } from 'lucide-react';
import { SignedIn, SignedOut, SignInButton, useAuth, useClerk, useUser } from '@clerk/clerk-react';

// Local: /api (Vite proxy) or 127.0.0.1:8080. Deployed: set VITE_API_BASE to your API URL (e.g. https://your-api.com:8080/api)
const API_BASE = import.meta.env.VITE_API_BASE || '/api';
const API_BASE_FALLBACK = 'http://127.0.0.1:8080/api';
const API_HOST = '';
const GUEST_CART_STORAGE_KEY = 'sportspic_cart_guest';
const LEGACY_CART_STORAGE_KEY = 'sportspic_cart';
const MAX_UPLOAD_FILE_MB = 25;
const ALLOWED_UPLOAD_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp']);

const getCartStorageKey = (userId) => (
  userId ? `sportspic_cart_user_${userId}` : GUEST_CART_STORAGE_KEY
);

const readCartFromStorage = (key) => {
  if (!key) return [];
  try {
    const saved = localStorage.getItem(key);
    if (!saved) return [];
    const parsed = JSON.parse(saved);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const mergeUniqueCartItems = (...carts) => {
  const seen = new Set();
  const merged = [];
  carts.flat().forEach((item) => {
    const id = item?.image_url;
    if (!id || seen.has(id)) return;
    seen.add(id);
    merged.push(item);
  });
  return merged;
};

const cartFingerprint = (items) => {
  try {
    return JSON.stringify(items || []);
  } catch {
    return '';
  }
};

function App() {
  const { getToken, isSignedIn } = useAuth();
  const { openSignIn, openUserProfile, signOut } = useClerk();
  const { user } = useUser();
  const [step, setStep] = useState(0);
  const [purchaseSuccessPhotos, setPurchaseSuccessPhotos] = useState([]);

  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [selectedSchool, setSelectedSchool] = useState(null);
  const [sports, setSports] = useState([]);
  const [selectedSport, setSelectedSport] = useState(null);
  const [schedule, setSchedule] = useState([]);
  const [selectedGame, setSelectedGame] = useState(null);
  const [clusters, setClusters] = useState([]);
  const [selectedCluster, setSelectedCluster] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState('');
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [pendingFiles, setPendingFiles] = useState([]);
  const [photoPrice, setPhotoPrice] = useState('');
  const [includeInPackage, setIncludeInPackage] = useState(true);
  const [lightboxPhoto, setLightboxPhoto] = useState(null);
  const [selectedPhotos, setSelectedPhotos] = useState([]);
  const [cart, setCart] = useState([]);
  const [activeCartStorageKey, setActiveCartStorageKey] = useState(GUEST_CART_STORAGE_KEY);
  const [cartInitialized, setCartInitialized] = useState(false);
  const [showCart, setShowCart] = useState(false);
  const [viewingProfileUserId, setViewingProfileUserId] = useState(null);
  const [viewingProfileDisplayName, setViewingProfileDisplayName] = useState('');
  const [profileData, setProfileData] = useState(null);
  const [profileInstagramEdit, setProfileInstagramEdit] = useState('');
  const [profilePackageDeals, setProfilePackageDeals] = useState([]);
  const [profilePackagesLoading, setProfilePackagesLoading] = useState(false);
  const [profilePackagesSaving, setProfilePackagesSaving] = useState(false);
  const [profilePackageSaveMessage, setProfilePackageSaveMessage] = useState('');
  const [salesStats, setSalesStats] = useState(null);
  const [cartQuote, setCartQuote] = useState(null);
  const [cartQuoteLoading, setCartQuoteLoading] = useState(false);
  const [photographerDealsByUser, setPhotographerDealsByUser] = useState({});
  const [showAccountDropdown, setShowAccountDropdown] = useState(false);
  const [showNotificationsDropdown, setShowNotificationsDropdown] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [notificationsUnreadCount, setNotificationsUnreadCount] = useState(0);
  const [instagramLinkMessage, setInstagramLinkMessage] = useState(null); // 'linked' | 'denied' | 'error' | null
  const fileInputRef = useRef(null);
  const clusterPollTimerRef = useRef(null);
  const accountDropdownRef = useRef(null);
  const notificationsDropdownRef = useRef(null);
  const cartSyncedFingerprintRef = useRef('');

  const fetchJsonWithFallback = async (path, options = {}) => {
    const tryRequest = async (base) => {
      const res = await fetch(`${base}${path}`, options);
      const text = await res.text();
      let data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        data = { error: text || 'Invalid server response' };
      }
      return { res, data };
    };

    try {
      return await tryRequest(API_BASE);
    } catch {
      return await tryRequest(API_BASE_FALLBACK);
    }
  };

  const getPrimaryPackageDealLabel = (uploaderId) => {
    if (!uploaderId) return 'Package Deal';
    const deals = photographerDealsByUser[uploaderId] || [];
    if (!deals.length) return 'Package Deal';
    const first = deals[0];
    return `${first.quantity} for $${(Number(first.package_price_cents) / 100).toFixed(2)}`;
  };

  const getAllPackageDealsLabel = (uploaderId) => {
    if (!uploaderId) return '';
    const deals = photographerDealsByUser[uploaderId] || [];
    if (!deals.length) return '';
    return deals.map((d) => `${d.quantity} for $${(Number(d.package_price_cents) / 100).toFixed(2)}`).join(' • ');
  };

  // Hydrate cart from server when signed in; keep local storage as guest/offline fallback.
  useEffect(() => {
    let cancelled = false;
    const userId = (isSignedIn && user?.id) ? user.id : null;
    const guestCart = readCartFromStorage(GUEST_CART_STORAGE_KEY);
    const legacyCart = readCartFromStorage(LEGACY_CART_STORAGE_KEY);
    setCartInitialized(false);

    if (userId) {
      const userKey = getCartStorageKey(userId);
      const userCart = readCartFromStorage(userKey);
      const hydrateSignedInCart = async () => {
        let token = null;
        let serverCart = [];
        try {
          token = await getToken();
          if (token) {
            const { res, data } = await fetchJsonWithFallback('/cart', {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (res.ok && Array.isArray(data?.items)) {
              serverCart = data.items;
            }
          }
        } catch {
          // Use local fallback when cart API is unavailable.
        }

        const mergedUserCart = mergeUniqueCartItems(serverCart, userCart, guestCart, legacyCart);
        if (cancelled) return;
        const mergedFingerprint = cartFingerprint(mergedUserCart);

        localStorage.setItem(userKey, JSON.stringify(mergedUserCart));
        localStorage.removeItem(GUEST_CART_STORAGE_KEY);
        localStorage.removeItem(LEGACY_CART_STORAGE_KEY);
        setActiveCartStorageKey(userKey);
        setCart(mergedUserCart);
        setCartInitialized(true);
        cartSyncedFingerprintRef.current = mergedFingerprint;

        if (token && mergedFingerprint !== cartFingerprint(serverCart)) {
          try {
            const { res } = await fetchJsonWithFallback('/cart', {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
              body: JSON.stringify({ items: mergedUserCart }),
            });
            if (!cancelled && res.ok) {
              cartSyncedFingerprintRef.current = mergedFingerprint;
            }
          } catch {
            // ignore
          }
        }
      };
      hydrateSignedInCart();
      return () => {
        cancelled = true;
      };
    }

    const guestMergedCart = mergeUniqueCartItems(guestCart, legacyCart);
    localStorage.setItem(GUEST_CART_STORAGE_KEY, JSON.stringify(guestMergedCart));
    if (legacyCart.length) localStorage.removeItem(LEGACY_CART_STORAGE_KEY);
    setActiveCartStorageKey(GUEST_CART_STORAGE_KEY);
    setCart(guestMergedCart);
    setCartInitialized(true);
    cartSyncedFingerprintRef.current = '';
    return () => {
      cancelled = true;
    };
  }, [isSignedIn, user?.id, getToken]);

  // Save cart to current storage key whenever it changes
  useEffect(() => {
    if (!cartInitialized) return;
    if (!activeCartStorageKey) return;
    localStorage.setItem(activeCartStorageKey, JSON.stringify(cart));
  }, [cart, activeCartStorageKey, cartInitialized]);

  // Sync cart updates to backend for signed-in users (cross-device persistence).
  useEffect(() => {
    if (!cartInitialized || !isSignedIn || !user?.id) return;
    const currentFingerprint = cartFingerprint(cart);
    if (currentFingerprint === cartSyncedFingerprintRef.current) return;
    let cancelled = false;

    const sync = async () => {
      try {
        const token = await getToken();
        if (!token) return;
        const { res } = await fetchJsonWithFallback('/cart', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ items: cart }),
        });
        if (!cancelled && res.ok) {
          cartSyncedFingerprintRef.current = currentFingerprint;
        }
      } catch {
        // ignore
      }
    };

    sync();
    return () => {
      cancelled = true;
    };
  }, [cart, cartInitialized, isSignedIn, user?.id, getToken]);

  useEffect(() => {
    const trimmed = searchQuery.trim();
    if (!trimmed) {
      setSearchResults([]);
      return;
    }
    let cancelled = false;
    const delayDebounce = setTimeout(async () => {
      const q = encodeURIComponent(trimmed);
      const tryFetch = async (base) => {
        const res = await fetch(`${base}/search-school?q=${q}`);
        if (!res.ok) return null;
        const data = await res.json();
        return Array.isArray(data) ? data : [];
      };
      try {
        let results = await tryFetch(API_BASE);
        if (cancelled) return;
        if (results === null) {
          results = await tryFetch(API_BASE_FALLBACK);
        }
        if (!cancelled) setSearchResults(results || []);
      } catch {
        if (!cancelled) {
          try {
            const results = await tryFetch(API_BASE_FALLBACK);
            if (!cancelled) setSearchResults(results || []);
          } catch {
            if (!cancelled) setSearchResults([]);
          }
        }
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(delayDebounce);
    };
  }, [searchQuery]);

  useEffect(() => () => {
    if (clusterPollTimerRef.current) {
      clearTimeout(clusterPollTimerRef.current);
    }
  }, []);

  // Read purchase success from URL after Stripe redirect
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const purchase = params.get('purchase');
    const photosParam = params.get('photos');
    if (purchase === 'success' && photosParam) {
      const names = photosParam.split(',').map(s => s.trim()).filter(Boolean);
      if (names.length > 0) {
        setPurchaseSuccessPhotos(names);
        setCart(prev => prev.filter(p => !names.includes(p.image_url)));
        window.history.replaceState({}, '', window.location.pathname + window.location.hash);
      }
    }
  }, []);

  // Handle Instagram OAuth callback
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const instagram = params.get('instagram');
    if (instagram === 'linked' || instagram === 'denied' || instagram === 'error') {
      setInstagramLinkMessage(instagram);
      if (instagram === 'linked' && user?.id) {
        setViewingProfileUserId(user.id);
        setViewingProfileDisplayName('');
      }
      window.history.replaceState({}, '', window.location.pathname + window.location.hash);
    }
  }, [user?.id]);

  // Open profile when user loads after Instagram link callback
  useEffect(() => {
    if (instagramLinkMessage === 'linked' && user?.id && !viewingProfileUserId) {
      setViewingProfileUserId(user.id);
      setViewingProfileDisplayName('');
    }
  }, [instagramLinkMessage, user?.id, viewingProfileUserId]);

  // Handle escape key to close lightbox and modals
  useEffect(() => {
    const handleEscape = (e) => {
      if (e.key === 'Escape') {
        if (lightboxPhoto) {
          setLightboxPhoto(null);
        } else if (showCart) {
          setShowCart(false);
        } else if (showUploadModal) {
          setShowUploadModal(false);
          setPendingFiles([]);
        } else if (purchaseSuccessPhotos.length > 0) {
          setPurchaseSuccessPhotos([]);
        } else if (viewingProfileUserId) {
          setViewingProfileUserId(null);
        } else if (showAccountDropdown) {
          setShowAccountDropdown(false);
        } else if (showNotificationsDropdown) {
          setShowNotificationsDropdown(false);
        }
      }
    };
    window.addEventListener('keydown', handleEscape);
    return () => window.removeEventListener('keydown', handleEscape);
  }, [lightboxPhoto, showCart, showUploadModal, purchaseSuccessPhotos.length, viewingProfileUserId, showAccountDropdown, showNotificationsDropdown]);

  // Load profile data and sales stats when opening profile
  useEffect(() => {
    if (!viewingProfileUserId) {
      setProfileData(null);
      setSalesStats(null);
      setProfilePackageDeals([]);
      setProfilePackageSaveMessage('');
      return;
    }
    setProfileData(null);
    setSalesStats(null);
    setProfilePackagesLoading(true);
    fetch(`${API_BASE}/profile/${encodeURIComponent(viewingProfileUserId)}`)
      .then(r => r.json())
      .then(setProfileData)
      .catch(() => setProfileData({}));
    fetch(`${API_BASE}/stats/sales/${encodeURIComponent(viewingProfileUserId)}`)
      .then(r => r.json())
      .then(setSalesStats)
      .catch(() => setSalesStats({ error: true }));
    fetchJsonWithFallback(`/photographer/packages?user_id=${encodeURIComponent(viewingProfileUserId)}`)
      .then(({ data }) => {
        const normalized = Array.isArray(data?.deals)
          ? data.deals.map(d => ({
            quantity: String(d.quantity ?? ''),
            package_price_cents: String(d.package_price_cents ?? ''),
          }))
          : [];
        setProfilePackageDeals(normalized.length > 0 ? normalized : [{ quantity: '', package_price_cents: '' }]);
      })
      .catch(() => setProfilePackageDeals([{ quantity: '', package_price_cents: '' }]))
      .finally(() => setProfilePackagesLoading(false));
  }, [viewingProfileUserId]);

  // Sync Instagram edit field when profile data loads (for own profile)
  useEffect(() => {
    if (viewingProfileUserId === user?.id && profileData) {
      setProfileInstagramEdit(profileData.instagram || '');
    }
  }, [viewingProfileUserId, user?.id, profileData]);

  useEffect(() => {
    const ids = new Set();
    (selectedCluster?.photos || []).forEach((p) => { if (p?.uploader_id) ids.add(p.uploader_id); });
    cart.forEach((p) => { if (p?.uploader_id) ids.add(p.uploader_id); });
    if (lightboxPhoto?.uploader_id) ids.add(lightboxPhoto.uploader_id);
    const missing = Array.from(ids).filter((id) => photographerDealsByUser[id] === undefined);
    if (!missing.length) return;
    let cancelled = false;
    Promise.all(
      missing.map(async (id) => {
        try {
          const { data } = await fetchJsonWithFallback(`/photographer/packages?user_id=${encodeURIComponent(id)}`);
          return [id, Array.isArray(data?.deals) ? data.deals : []];
        } catch {
          return [id, []];
        }
      })
    ).then((entries) => {
      if (cancelled) return;
      setPhotographerDealsByUser((prev) => {
        const next = { ...prev };
        entries.forEach(([id, deals]) => {
          next[id] = deals;
        });
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [selectedCluster, cart, lightboxPhoto, photographerDealsByUser]);

  useEffect(() => {
    if (!showCart) {
      setCartQuote(null);
      setCartQuoteLoading(false);
      return;
    }
    if (cart.length === 0) {
      setCartQuote(null);
      setCartQuoteLoading(false);
      return;
    }
    let cancelled = false;
    setCartQuoteLoading(true);
    fetchJsonWithFallback('/package-quote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        items: cart.map(photo => ({
          photo_name: photo.image_url,
          price_cents: Math.round((photo.price != null ? Number(photo.price) : 5) * 100),
        })),
      }),
    })
      .then(({ data }) => {
        if (!cancelled) setCartQuote(data);
      })
      .catch(() => {
        if (!cancelled) setCartQuote(null);
      })
      .finally(() => {
        if (!cancelled) setCartQuoteLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showCart, cart]);

  // Close account dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (showAccountDropdown && accountDropdownRef.current && !accountDropdownRef.current.contains(e.target)) {
        setShowAccountDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showAccountDropdown]);

  // Fetch notifications when signed in
  useEffect(() => {
    if (!isSignedIn || !user?.id) {
      setNotifications([]);
      setNotificationsUnreadCount(0);
      return;
    }
    const fetchNotifications = async () => {
      try {
        const token = await getToken();
        if (!token) return;
        const res = await fetch(`${API_BASE}/notifications`, { headers: { Authorization: `Bearer ${token}` } });
        if (res.ok) {
          const data = await res.json();
          setNotifications(data.notifications || []);
          setNotificationsUnreadCount(data.unread_count ?? 0);
        }
      } catch {
        // ignore
      }
    };
    fetchNotifications();
    const interval = setInterval(fetchNotifications, 60000); // poll every minute
    return () => clearInterval(interval);
  }, [isSignedIn, user?.id, getToken]);

  // Close notifications dropdown when clicking outside
  useEffect(() => {
    const handleClick = (e) => {
      if (showNotificationsDropdown && notificationsDropdownRef.current && !notificationsDropdownRef.current.contains(e.target)) {
        setShowNotificationsDropdown(false);
      }
    };
    document.addEventListener('click', handleClick);
    return () => document.removeEventListener('click', handleClick);
  }, [showNotificationsDropdown]);

  const markNotificationsRead = async (ids = []) => {
    try {
      const token = await getToken();
      if (!token) return;
      await fetch(`${API_BASE}/notifications/mark-read`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ ids }),
      });
      setNotifications(prev => prev.map(n => ({ ...n, read: true })));
      setNotificationsUnreadCount(0);
    } catch {
      // ignore
    }
  };

  const handleDownloadPhoto = async (photoName) => {
    try {
      const token = await getToken();
      if (!token) {
        alert('Please sign in to download your photos.');
        return;
      }
      const res = await fetch(`${API_BASE}/photo/${encodeURIComponent(photoName)}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.error || 'Download failed. You may need to sign in again.');
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = photoName;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error(err);
      alert('Download failed. Please try again.');
    }
  };

  const handleSelectSchool = async (school) => {
    setSelectedSchool(school);
    setSearchResults([]);
    setSearchQuery('');
    try {
      const res = await fetch(`${API_BASE}/school/${encodeURIComponent(school.name)}/sports`);
      const data = await res.json();
      setSports(data);
      setStep(1);
    } catch (err) {
      console.error(err);
    }
  };

  const handleSelectSport = async (sport) => {
    setSelectedSport(sport);
    try {
      const res = await fetch(`${API_BASE}/schedule?school=${encodeURIComponent(selectedSchool.name)}&sport=${encodeURIComponent(sport)}`);
      const data = await res.json();
      setSchedule(data);
      setStep(2);
    } catch (err) {
      console.error(err);
    }
  };

  const handleSelectGame = async (game) => {
    setSelectedGame(game);
    setUploadMessage('');
    try {
      if (isSignedIn) {
        const token = await getToken();
        if (token) {
          await fetch(`${API_BASE}/claim-uploader`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
            body: JSON.stringify({
              display_name: user?.username || user?.primaryEmailAddress?.emailAddress || '',
              image_url: user?.imageUrl || '',
              notification_email: user?.primaryEmailAddress?.emailAddress || '',
            }),
          });
        }
      }
      const res = await fetch(`${API_BASE}/game/${game.game_id}/clusters`);
      const data = await res.json();
      setClusters(data);
      setStep(3);
    } catch (err) {
      console.error(err);
    }
  };

  const handleOpenCluster = async (clusterId) => {
    try {
      const gameId = selectedGame?.game_id;
      const url = gameId != null
        ? `${API_BASE}/clusters/${encodeURIComponent(clusterId)}?game_id=${gameId}`
        : `${API_BASE}/clusters/${encodeURIComponent(clusterId)}`;
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error('Cluster not found');
      }
      const data = await res.json();
      setSelectedCluster(data);
      setSelectedPhotos([]); // Clear selection when entering a new cluster
      setStep(4);
    } catch (err) {
      console.error(err);
    }
  };

  const goBack = () => {
    if (step > 0) {
      setStep(step - 1);
    }
  };

  const resetAll = () => {
    setStep(0);
    setSelectedSchool(null);
    setSelectedSport(null);
    setSelectedGame(null);
    setSelectedCluster(null);
    setSelectedPhotos([]);
    setSports([]);
    setSchedule([]);
    setClusters([]);
    setUploadMessage('');
    setUploading(false);
  };

  const refreshGameClusters = async (gameId) => {
    const res = await fetch(`${API_BASE}/game/${gameId}/clusters`);
    const data = await res.json();
    setClusters(data);
  };

  const pollClusterCompletion = async (gameId, attemptsLeft = 60) => {
    try {
      const res = await fetch(`${API_BASE}/clustering/status`);
      if (!res.ok) {
        return;
      }
      const status = await res.json();
      if (!status.running) {
        await refreshGameClusters(gameId);
        if (status.last_success === false) {
          setUploadMessage('Photos uploaded, but clustering failed. Check backend logs.');
        } else {
          setUploadMessage('Photos uploaded and clusters updated.');
        }
        return;
      }
      if (attemptsLeft > 0) {
        clusterPollTimerRef.current = setTimeout(() => pollClusterCompletion(gameId, attemptsLeft - 1), 3000);
      } else {
        setUploadMessage('Photos uploaded. Clustering is still running in the background.');
      }
    } catch (err) {
      console.error(err);
    }
  };

  const pollCloudJobCompletion = async (jobId, gameId, token, attemptsLeft = 120) => {
    try {
      const res = await fetch(`${API_BASE}/jobs/${encodeURIComponent(jobId)}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        setUploadMessage('Photos uploaded. Clustering is running in the cloud.');
        return;
      }
      const job = await res.json();
      if (job.status === 'completed') {
        await refreshGameClusters(gameId);
        setUploadMessage('Photos uploaded and cloud clustering completed.');
        return;
      }
      if (job.status === 'failed') {
        setUploadMessage(`Photos uploaded, but cloud clustering failed${job.error ? `: ${job.error}` : '.'}`);
        return;
      }
      if (attemptsLeft > 0) {
        clusterPollTimerRef.current = setTimeout(
          () => pollCloudJobCompletion(jobId, gameId, token, attemptsLeft - 1),
          3000
        );
      } else {
        setUploadMessage('Photos uploaded. Cloud clustering is still processing.');
      }
    } catch (err) {
      console.error(err);
      setUploadMessage('Photos uploaded. Cloud clustering status unavailable right now.');
    }
  };

  const handleFileSelection = (event) => {
    const rawFiles = Array.from(event.target.files || []);
    if (!selectedGame) {
      setUploadMessage('Open a game first, then upload photos.');
      return;
    }
    if (!rawFiles.length) {
      setUploadMessage('Select at least one photo to upload.');
      return;
    }
    const maxBytes = MAX_UPLOAD_FILE_MB * 1024 * 1024;
    const accepted = [];
    let rejectedExt = 0;
    let rejectedSize = 0;
    for (const file of rawFiles) {
      const name = (file?.name || '').toLowerCase();
      const dot = name.lastIndexOf('.');
      const ext = dot >= 0 ? name.slice(dot) : '';
      if (!ALLOWED_UPLOAD_EXTS.has(ext)) {
        rejectedExt += 1;
        continue;
      }
      if ((file?.size || 0) > maxBytes) {
        rejectedSize += 1;
        continue;
      }
      accepted.push(file);
    }
    if (!accepted.length) {
      setUploadMessage(
        `No valid files selected. Allowed: .jpg, .jpeg, .png, .webp, max ${MAX_UPLOAD_FILE_MB}MB each.`
      );
      if (event?.target) event.target.value = '';
      return;
    }
    if (rejectedExt || rejectedSize) {
      setUploadMessage(
        `Selected ${accepted.length} file(s). Skipped ${rejectedExt} unsupported and ${rejectedSize} over ${MAX_UPLOAD_FILE_MB}MB.`
      );
    }
    // Store files and show the upload modal for price/package options
    setPendingFiles(accepted);
    setPhotoPrice('');
    setIncludeInPackage(true);
    setShowUploadModal(true);
    // Clear the input so the same files can be re-selected if needed
    if (event?.target) {
      event.target.value = '';
    }
  };

  const handleUploadConfirm = async () => {
    if (!pendingFiles.length) return;

    setShowUploadModal(false);
    setUploading(true);
    setUploadMessage('Uploading...');

    const photographerLabel = user?.username || user?.primaryEmailAddress?.emailAddress || 'Game Upload';
    const formData = new FormData();
    pendingFiles.forEach((file) => formData.append('photos', file));
    formData.append('school', selectedSchool?.name || '');
    formData.append('sport', selectedSport || '');
    formData.append('photographer', photographerLabel);
    formData.append('price', String(photoPrice ?? '0'));
    formData.append('include_in_package', includeInPackage ? 'true' : 'false');

    try {
      // Get auth token for upload
      const token = await getToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      if (!token) {
        setUploadMessage('Please sign in to upload photos.');
        return;
      }

      setUploadMessage('Uploading to local backend...');
      let res = await fetch(`${API_BASE}/game/${selectedGame.game_id}/upload`, {
        method: 'POST',
        body: formData,
        headers,
      });
      // Backward compatibility: older backend may not expose per-game upload yet.
      if (res.status === 404) {
        const fallbackForm = new FormData();
        pendingFiles.forEach((file) => fallbackForm.append('photos', file));
        fallbackForm.append('school', selectedSchool?.name || '');
        fallbackForm.append('sport', selectedSport || '');
        fallbackForm.append('game_id', String(selectedGame.game_id));
        fallbackForm.append('photographer', photographerLabel);
        fallbackForm.append('price', String(photoPrice ?? '0'));
        fallbackForm.append('include_in_package', includeInPackage ? 'true' : 'false');
        res = await fetch(`${API_BASE}/photographer/upload`, {
          method: 'POST',
          body: fallbackForm,
          headers,
        });
      }

      const rawBody = await res.text();
      let data = {};
      try {
        data = rawBody ? JSON.parse(rawBody) : {};
      } catch {
        data = { error: rawBody || 'Upload failed.' };
      }
      if (!res.ok) {
        if (res.status === 404) {
          setUploadMessage('Upload endpoint not found (404). Restart backend server from latest app.py and try again.');
          return;
        }
        setUploadMessage(data?.error || `Upload failed (${res.status}).`);
        return;
      }
      setUploadMessage(`Uploaded ${data.uploaded_count} photo(s). Clustering in progress...`);
      if (Array.isArray(data.clusters)) {
        setClusters(data.clusters);
      } else {
        await refreshGameClusters(selectedGame.game_id);
      }
      if (clusterPollTimerRef.current) {
        clearTimeout(clusterPollTimerRef.current);
      }
      pollClusterCompletion(selectedGame.game_id);
      setSelectedCluster(null);
    } catch (err) {
      console.error(err);
      setUploadMessage('Upload failed due to a network or server error.');
    } finally {
      setUploading(false);
      setPendingFiles([]);
    }
  };

  const handleUploadCancel = () => {
    setShowUploadModal(false);
    setPendingFiles([]);
    setPhotoPrice('');
    setIncludeInPackage(true);
  };

  const handleBuyPhoto = async (photo) => {
    try {
      const body = {
        photo_name: photo.image_url,
        price_cents: Math.round((photo.price != null ? Number(photo.price) : 5) * 100),
      };
      if (user?.id) body.clerk_user_id = user.id;
      if (user?.primaryEmailAddress?.emailAddress) body.customer_email = user.primaryEmailAddress.emailAddress;
      const res = await fetch(`${API_BASE}/create-checkout-session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.checkout_url) {
        window.location.href = data.checkout_url;
      } else {
        alert(data.error || 'Failed to start checkout');
      }
    } catch (err) {
      console.error(err);
      alert('Failed to start checkout. Please try again.');
    }
  };

  const togglePhotoSelection = (photo) => {
    setSelectedPhotos(prev => {
      const isSelected = prev.some(p => p.image_url === photo.image_url);
      if (isSelected) {
        return prev.filter(p => p.image_url !== photo.image_url);
      } else {
        return [...prev, photo];
      }
    });
  };

  const clearSelection = () => {
    setSelectedPhotos([]);
  };

  const getSelectionTotal = () => {
    return selectedPhotos.reduce((sum, p) => sum + (p.price != null ? Number(p.price) : 5), 0);
  };

  const getCartTotal = () => {
    return cart.reduce((sum, p) => sum + (p.price != null ? Number(p.price) : 5), 0);
  };

  const addToCart = (photos) => {
    console.log('addToCart called with:', photos);
    setCart(prev => {
      const newPhotos = photos.filter(
        photo => !prev.some(p => p.image_url === photo.image_url)
      );
      console.log('Adding to cart:', newPhotos);
      return [...prev, ...newPhotos];
    });
  };

  const removeFromCart = (photo) => {
    setCart(prev => prev.filter(p => p.image_url !== photo.image_url));
  };

  const clearCart = () => {
    setCart([]);
  };

  const handleAddSelectedToCart = () => {
    console.log('handleAddSelectedToCart called, selectedPhotos:', selectedPhotos);
    if (selectedPhotos.length === 0) return;
    addToCart(selectedPhotos);
    setSelectedPhotos([]);
    setShowCart(true);
  };

  const handleCheckout = async () => {
    if (cart.length === 0) return;
    try {
      const body = {
        items: cart.map(photo => ({
          photo_name: photo.image_url,
          price_cents: Math.round((photo.price != null ? Number(photo.price) : 5) * 100),
        })),
      };
      if (user?.id) body.clerk_user_id = user.id;
      if (user?.primaryEmailAddress?.emailAddress) body.customer_email = user.primaryEmailAddress.emailAddress;
      const res = await fetch(`${API_BASE}/create-checkout-session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.checkout_url) {
        window.location.href = data.checkout_url;
      } else {
        alert(data.error || 'Failed to start checkout');
      }
    } catch (err) {
      console.error(err);
      alert('Failed to start checkout. Please try again.');
    }
  };

  const updateProfilePackageDeal = (index, key, value) => {
    setProfilePackageDeals(prev => prev.map((deal, i) => (
      i === index ? { ...deal, [key]: value } : deal
    )));
  };

  const addProfilePackageDealRow = () => {
    setProfilePackageDeals(prev => [...prev, { quantity: '', package_price_cents: '' }]);
  };

  const removeProfilePackageDealRow = (index) => {
    setProfilePackageDeals(prev => {
      const next = prev.filter((_, i) => i !== index);
      return next.length > 0 ? next : [{ quantity: '', package_price_cents: '' }];
    });
  };

  const saveProfilePackageDeals = async () => {
    try {
      setProfilePackagesSaving(true);
      setProfilePackageSaveMessage('');
      const token = await getToken();
      if (!token) {
        setProfilePackageSaveMessage('Please sign in again and retry.');
        return;
      }
      const deals = profilePackageDeals
        .map(d => {
          const quantity = Number(d.quantity);
          const cents = Number(d.package_price_cents);
          return {
            quantity: Number.isFinite(quantity) ? Math.round(quantity) : 0,
            package_price_cents: Number.isFinite(cents) ? Math.round(cents) : 0,
          };
        })
        .filter(d => d.quantity >= 2 && d.package_price_cents >= 50);

      const { res, data } = await fetchJsonWithFallback('/photographer/packages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ deals }),
      });
      if (!res.ok) {
        setProfilePackageSaveMessage(data?.error || `Failed to save package deals (HTTP ${res.status}).`);
        return;
      }
      const normalized = Array.isArray(data?.deals)
        ? data.deals.map(d => ({
          quantity: String(d.quantity ?? ''),
          package_price_cents: String(d.package_price_cents ?? ''),
        }))
        : [];
      setProfilePackageDeals(normalized.length > 0 ? normalized : [{ quantity: '', package_price_cents: '' }]);
      setProfilePackageSaveMessage('Package deals saved.');
    } catch {
      setProfilePackageSaveMessage('Failed to save package deals.');
    } finally {
      setProfilePackagesSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-white text-gray-800 font-sans">
      {/* Purchase confirmed modal */}
      {purchaseSuccessPhotos.length > 0 && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50" onClick={() => setPurchaseSuccessPhotos([])}>
          <div className="bg-white rounded-2xl shadow-xl max-w-lg w-full max-h-[90vh] overflow-hidden flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="p-6 border-b border-gray-100 flex items-center gap-3">
              <div className="w-12 h-12 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
                <CheckCircle className="w-7 h-7 text-green-600" />
              </div>
              <div>
                <h2 className="text-xl font-bold text-gray-900">Purchase confirmed!</h2>
                <p className="text-sm text-gray-500">Your photos are ready to download.</p>
              </div>
            </div>
            <div className="p-6 overflow-y-auto flex-1">
              <p className="text-sm text-gray-600 mb-4">
                We&apos;ve also sent your photos and a receipt to the email on your account.
              </p>
              <ul className="space-y-2">
                {purchaseSuccessPhotos.map((name) => (
                  <li key={name} className="flex items-center justify-between gap-3 py-2 border-b border-gray-100 last:border-0">
                    <span className="text-sm text-gray-700 truncate flex-1" title={name}>{name}</span>
                    <button
                      type="button"
                      onClick={() => handleDownloadPhoto(name)}
                      className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[#e53935] text-white text-sm font-medium hover:bg-[#c62828] transition-colors flex-shrink-0"
                    >
                      <Download className="w-4 h-4" />
                      Download
                    </button>
                  </li>
                ))}
              </ul>
            </div>
            <div className="p-4 border-t border-gray-100 flex justify-end">
              <button
                type="button"
                onClick={() => setPurchaseSuccessPhotos([])}
                className="px-4 py-2 rounded-lg bg-gray-100 text-gray-700 font-medium hover:bg-gray-200 transition-colors"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Profile / Bio page overlay */}
      {viewingProfileUserId && (viewingProfileUserId !== user?.id || isSignedIn) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 overflow-y-auto" onClick={() => setViewingProfileUserId(null)}>
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full overflow-hidden my-8" onClick={e => e.stopPropagation()}>
            <div className="p-6 border-b border-gray-100 flex items-center justify-between">
              <h2 className="text-xl font-bold text-gray-900">Profile</h2>
              <button onClick={() => setViewingProfileUserId(null)} className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-6 space-y-6">
              {/* Avatar & name */}
              <div className="flex flex-col items-center text-center">
                {(() => {
                  const isOwn = viewingProfileUserId === user?.id;
                  const imgSrc = isOwn ? user?.imageUrl : profileData?.image_url;
                  const initial = isOwn ? (user?.username || user?.primaryEmailAddress?.emailAddress || 'U').charAt(0).toUpperCase() : (viewingProfileDisplayName || profileData?.display_name || 'P').charAt(0).toUpperCase();
                  return (
                    <div className="w-24 h-24 rounded-full overflow-hidden bg-[#e53935] flex items-center justify-center text-white text-3xl font-bold relative">
                      {imgSrc && <img src={imgSrc} alt="" className="absolute inset-0 w-full h-full object-cover z-[1]" referrerPolicy="no-referrer" onError={(e) => { e.target.style.display = 'none'; }} />}
                      <span className="relative z-0">{initial}</span>
                    </div>
                  );
                })()}
                <p className="font-semibold text-gray-900 text-lg mt-3">
                  {viewingProfileUserId === user?.id ? (user?.username || 'User') : (viewingProfileDisplayName || profileData?.display_name || 'Photographer')}
                </p>
                {viewingProfileUserId === user?.id && user?.primaryEmailAddress?.emailAddress && (
                  <p className="text-sm text-gray-500">{user.primaryEmailAddress.emailAddress}</p>
                )}
              </div>

              {/* Instagram */}
              <div>
                {instagramLinkMessage && viewingProfileUserId === user?.id && (
                  <div className={`mb-3 px-4 py-2 rounded-lg text-sm ${instagramLinkMessage === 'linked' ? 'bg-green-50 text-green-800' : instagramLinkMessage === 'denied' ? 'bg-amber-50 text-amber-800' : 'bg-red-50 text-red-800'}`}>
                    {instagramLinkMessage === 'linked' && 'Instagram linked successfully!'}
                    {instagramLinkMessage === 'denied' && 'Instagram connection was cancelled.'}
                    {instagramLinkMessage === 'error' && 'Something went wrong. Please try again.'}
                    <button type="button" onClick={() => setInstagramLinkMessage(null)} className="ml-2 underline">Dismiss</button>
                  </div>
                )}
                {viewingProfileUserId === user?.id ? (
                  <div className="space-y-2">
                    <label className="block text-sm font-medium text-gray-700">Instagram</label>
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          const token = await getToken();
                          if (!token) return;
                          const res = await fetch(`${API_BASE}/instagram/connect`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                            body: JSON.stringify({ return_url: window.location.origin + window.location.pathname }),
                          });
                          const data = await res.json();
                          if (data.redirect_url) {
                            window.location.href = data.redirect_url;
                          } else {
                            setInstagramLinkMessage('error');
                          }
                        } catch {
                          setInstagramLinkMessage('error');
                        }
                      }}
                      className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#E4405F] text-[#E4405F] hover:bg-[#E4405F]/10 transition-colors text-sm font-medium"
                    >
                      <Instagram className="w-4 h-4" />
                      Link with Instagram
                    </button>
                    <p className="text-xs text-gray-500">Or enter manually:</p>
                    <div className="flex gap-2">
                      <span className="flex items-center px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-gray-500">@</span>
                      <input
                        type="text"
                        placeholder="username"
                        value={profileInstagramEdit}
                        onChange={(e) => setProfileInstagramEdit(e.target.value)}
                        className="flex-1 px-3 py-2 border border-gray-200 rounded-lg focus:border-[#e53935] focus:ring-1 focus:ring-[#e53935]/20"
                      />
                      <button
                        onClick={async () => {
                          const token = await getToken();
                          if (!token) return;
                          const res = await fetch(`${API_BASE}/profile`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                            body: JSON.stringify({
                              instagram: profileInstagramEdit,
                              display_name: user?.username || user?.primaryEmailAddress?.emailAddress || '',
                              image_url: user?.imageUrl || '',
                              notification_email: user?.primaryEmailAddress?.emailAddress || '',
                            }),
                          });
                          if (res.ok) setProfileData(prev => ({ ...prev, instagram: profileInstagramEdit }));
                        }}
                        className="px-4 py-2 bg-[#e53935] text-white font-medium rounded-lg hover:bg-[#c62828] transition-colors"
                      >
                        Save
                      </button>
                    </div>
                  </div>
                ) : profileData === null ? (
                  <p className="text-sm text-gray-500">Loading...</p>
                ) : (profileData?.instagram) ? (
                  <a
                    href={`https://instagram.com/${profileData.instagram.replace(/^@/, '')}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-3 px-4 py-3 rounded-xl border border-gray-200 hover:border-[#e53935]/30 hover:bg-[#e53935]/5 transition-colors"
                  >
                    <Instagram className="w-5 h-5 text-[#E4405F]" />
                    <span className="font-medium text-gray-800">@{profileData.instagram.replace(/^@/, '')}</span>
                    <ChevronRight className="w-4 h-4 text-gray-400 ml-auto" />
                  </a>
                ) : (
                  <p className="text-sm text-gray-500">No Instagram linked</p>
                )}
              </div>

              {/* Sales stats (all profiles) */}
              <div className="border-t border-gray-100 pt-6">
                <h3 className="font-semibold text-gray-900 mb-4">Sales</h3>
                  {salesStats?.error ? (
                    <p className="text-gray-500 text-sm">Unable to load stats.</p>
                  ) : salesStats ? (
                    <div className="grid grid-cols-2 gap-4">
                      <div className="p-4 bg-[#e53935]/5 border border-[#e53935]/20 rounded-xl">
                        <p className="text-sm text-gray-500 mb-1">Total Sales</p>
                        <p className="text-2xl font-bold text-[#e53935]">${salesStats.total_sales_dollars?.toFixed(2) ?? '0.00'}</p>
                      </div>
                      <div className="p-4 bg-gray-50 border border-gray-100 rounded-xl">
                        <p className="text-sm text-gray-500 mb-1">Photos Uploaded</p>
                        <p className="text-2xl font-bold text-gray-900">{salesStats.photos_uploaded ?? 0}</p>
                      </div>
                    </div>
                  ) : (
                    <p className="text-gray-500 text-sm">Loading...</p>
                  )}
                  {salesStats && (
                    <p className="text-sm text-gray-500 mt-3">{salesStats.purchase_count ?? 0} purchase{salesStats.purchase_count !== 1 ? 's' : ''} completed</p>
                  )}
                </div>

              {/* Package deals */}
              <div className="border-t border-gray-100 pt-6">
                <h3 className="font-semibold text-gray-900 mb-4">Package Deals</h3>
                {profilePackagesLoading ? (
                  <p className="text-sm text-gray-500">Loading package deals...</p>
                ) : viewingProfileUserId === user?.id ? (
                  <div className="space-y-3">
                    <p className="text-xs text-gray-500">
                      Set bundle pricing buyers can get automatically at checkout. Minimum package price is $0.50.
                    </p>
                    {profilePackageDeals.map((deal, index) => (
                      <div key={`deal-${index}`} className="grid grid-cols-12 gap-2 items-center">
                        <div className="col-span-4">
                          <label className="text-xs text-gray-500">Photos</label>
                          <input
                            type="number"
                            min="2"
                            step="1"
                            value={deal.quantity}
                            onChange={(e) => updateProfilePackageDeal(index, 'quantity', e.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-[#e53935] focus:outline-none focus:ring-1 focus:ring-[#e53935]/30"
                            placeholder="3"
                          />
                        </div>
                        <div className="col-span-5">
                          <label className="text-xs text-gray-500">Package Price ($)</label>
                          <input
                            type="number"
                            min="0.5"
                            step="0.01"
                            value={deal.package_price_cents === '' ? '' : (Number(deal.package_price_cents) / 100)}
                            onChange={(e) => {
                              const dollars = Number(e.target.value);
                              const cents = Number.isFinite(dollars) ? Math.round(dollars * 100) : '';
                              updateProfilePackageDeal(index, 'package_price_cents', cents === '' ? '' : String(cents));
                            }}
                            className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-[#e53935] focus:outline-none focus:ring-1 focus:ring-[#e53935]/30"
                            placeholder="12.00"
                          />
                        </div>
                        <div className="col-span-3 flex justify-end pt-5">
                          <button
                            type="button"
                            onClick={() => removeProfilePackageDealRow(index)}
                            className="text-xs text-gray-500 hover:text-[#e53935]"
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    ))}
                    <div className="flex items-center gap-3 pt-1">
                      <button
                        type="button"
                        onClick={addProfilePackageDealRow}
                        className="px-3 py-2 text-xs font-semibold rounded-lg border border-gray-200 text-gray-700 hover:border-[#e53935]/40 hover:bg-[#e53935]/5 transition-colors"
                      >
                        Add Deal
                      </button>
                      <button
                        type="button"
                        onClick={saveProfilePackageDeals}
                        disabled={profilePackagesSaving}
                        className="px-4 py-2 text-xs font-semibold rounded-lg bg-[#e53935] text-white hover:bg-[#c62828] transition-colors disabled:opacity-60"
                      >
                        {profilePackagesSaving ? 'Saving...' : 'Save Deals'}
                      </button>
                    </div>
                    {profilePackageSaveMessage && (
                      <p className={`text-xs ${profilePackageSaveMessage.includes('saved') ? 'text-green-600' : 'text-red-600'}`}>
                        {profilePackageSaveMessage}
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2">
                    {profilePackageDeals.filter(d => Number(d.quantity) >= 2 && Number(d.package_price_cents) >= 50).length > 0 ? (
                      profilePackageDeals
                        .filter(d => Number(d.quantity) >= 2 && Number(d.package_price_cents) >= 50)
                        .map((deal, idx) => (
                          <div key={`public-deal-${idx}`} className="flex items-center justify-between px-4 py-3 rounded-xl border border-gray-200 bg-gray-50">
                            <span className="text-sm font-medium text-gray-800">{deal.quantity} photos</span>
                            <span className="text-sm font-bold text-[#e53935]">${(Number(deal.package_price_cents) / 100).toFixed(2)}</span>
                          </div>
                        ))
                    ) : (
                      <p className="text-sm text-gray-500">No package deals available.</p>
                    )}
                  </div>
                )}
              </div>

              {/* Manage account (own profile only) */}
              {viewingProfileUserId === user?.id && (
                <button
                  type="button"
                  onClick={() => { openUserProfile?.(); setViewingProfileUserId(null); }}
                  className="w-full flex items-center gap-3 px-4 py-3 rounded-xl border border-gray-200 hover:border-[#e53935]/30 hover:bg-[#e53935]/5 transition-colors text-left"
                >
                  <Settings className="w-5 h-5 text-gray-500" />
                  <span className="font-medium text-gray-800">Manage account</span>
                  <ChevronRight className="w-4 h-4 text-gray-400 ml-auto" />
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Top announcement bar */}
      <div className="bg-[#e53935] text-white text-center py-2 text-sm font-medium">
        Get 15% Off on Game Day Packages! <span className="underline cursor-pointer ml-2">Shop Now</span>
      </div>

      <header className="bg-white border-b border-gray-100 sticky top-0 z-40 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
          <button onClick={resetAll} className="flex items-center gap-2">
            <div className="w-8 h-8 bg-[#e53935] rounded-lg flex items-center justify-center">
              <Camera className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold text-gray-900">SportsPic</span>
          </button>

            {/* Navigation */}
            <nav className="hidden md:flex items-center gap-8">
              <button onClick={resetAll} className="text-sm font-medium text-gray-600 hover:text-[#e53935] transition-colors">
                Home
              </button>
            </nav>

            {/* Right Actions */}
            <div className="flex items-center gap-4">
              {/* Cart */}
              <button 
                onClick={() => setShowCart(true)}
                className="relative p-2 text-gray-500 hover:text-[#e53935] transition-colors"
              >
                <ShoppingCart className="w-6 h-6" />
                {cart.length > 0 && (
                  <span className="absolute -top-1 -right-1 w-5 h-5 bg-[#e53935] text-white text-xs font-bold rounded-full flex items-center justify-center">
                    {cart.length}
                  </span>
                )}
              </button>

              {/* Notifications (photographers only when signed in) */}
              <SignedIn>
                <div className="relative" ref={notificationsDropdownRef}>
                  <button
                    onClick={() => {
                      const willOpen = !showNotificationsDropdown;
                      setShowNotificationsDropdown(willOpen);
                      if (willOpen && notificationsUnreadCount > 0) {
                        markNotificationsRead();
                      }
                    }}
                    className="relative p-2 text-gray-500 hover:text-[#e53935] transition-colors"
                  >
                    <Bell className="w-6 h-6" />
                    {notificationsUnreadCount > 0 && (
                      <span className="absolute top-0.5 right-0.5 w-4 h-4 bg-[#e53935] text-white text-[10px] font-bold rounded-full flex items-center justify-center">
                        {notificationsUnreadCount > 9 ? '9+' : notificationsUnreadCount}
                      </span>
                    )}
                  </button>
                  {showNotificationsDropdown && (
                    <div className="absolute right-0 mt-2 w-80 max-h-96 overflow-y-auto bg-white rounded-xl shadow-lg border border-gray-100 py-1 z-50">
                      <div className="px-4 py-2 border-b border-gray-100 flex items-center justify-between">
                        <span className="font-semibold text-gray-900">Notifications</span>
                        {notificationsUnreadCount > 0 && (
                          <button
                            onClick={() => markNotificationsRead()}
                            className="text-xs text-[#e53935] hover:underline"
                          >
                            Mark all read
                          </button>
                        )}
                      </div>
                      {notifications.length === 0 ? (
                        <p className="px-4 py-6 text-sm text-gray-500 text-center">No notifications yet</p>
                      ) : (
                        <div className="divide-y divide-gray-50">
                          {notifications.map((n) => (
                            <div
                              key={n.id}
                              className={`px-4 py-3 text-left hover:bg-gray-50 transition-colors ${!n.read ? 'bg-[#e53935]/5' : ''}`}
                            >
                              {n.type === 'purchase' && (
                                <>
                                  <p className="text-sm font-medium text-gray-900">
                                    Someone purchased {n.photo_names?.length || 0} of your photo{n.photo_names?.length !== 1 ? 's' : ''}
                                  </p>
                                  <p className="text-xs text-gray-500 mt-0.5">
                                    Your share: ${((n.amount_cents || 0) / 100).toFixed(2)}
                                  </p>
                                  {n.photo_names?.length > 0 && (
                                    <p className="text-xs text-gray-400 mt-1 truncate">
                                      {n.photo_names.slice(0, 2).join(', ')}
                                      {n.photo_names.length > 2 ? ` +${n.photo_names.length - 2} more` : ''}
                                    </p>
                                  )}
                                </>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </SignedIn>

              {/* Account */}
              <SignedOut>
                <SignInButton mode="modal">
                  <button className="flex items-center gap-2 text-sm font-medium text-gray-600 hover:text-[#e53935] transition-colors">
                    <LogIn className="w-5 h-5" />
                    <span className="hidden sm:inline">Account</span>
                  </button>
                </SignInButton>
              </SignedOut>
              <SignedIn>
                <div className="relative" ref={accountDropdownRef}>
                  <button
                    onClick={() => setShowAccountDropdown(!showAccountDropdown)}
                    className="flex items-center justify-center w-8 h-8 rounded-full overflow-hidden border-2 border-gray-200 hover:border-[#e53935]/50 transition-colors focus:outline-none focus:ring-2 focus:ring-[#e53935]/30"
                  >
                    {user?.imageUrl ? (
                      <img src={user.imageUrl} alt="" className="w-full h-full object-cover" referrerPolicy="no-referrer" />
                    ) : (
                      <div className="w-full h-full bg-[#e53935] flex items-center justify-center text-white font-bold text-sm">
                        {(user?.username || user?.primaryEmailAddress?.emailAddress || 'U').charAt(0).toUpperCase()}
                      </div>
                    )}
                  </button>
                  {showAccountDropdown && (
                    <div className="absolute right-0 mt-2 w-56 bg-white rounded-xl shadow-lg border border-gray-100 py-1 z-50">
                      <button
                        onClick={() => { setViewingProfileUserId(user?.id); setViewingProfileDisplayName(''); setShowAccountDropdown(false); }}
                        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
                      >
                        <User className="w-4 h-4 text-gray-500" />
                        <span className="font-medium text-gray-800">My Profile</span>
                      </button>
                      <button
                        onClick={() => { openUserProfile?.(); setShowAccountDropdown(false); }}
                        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
                      >
                        <Settings className="w-4 h-4 text-gray-500" />
                        <span className="font-medium text-gray-800">Manage account</span>
                      </button>
                      <button
                        onClick={() => { signOut?.(); setShowAccountDropdown(false); }}
                        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors text-left"
                      >
                        <LogOut className="w-4 h-4 text-gray-500" />
                        <span className="font-medium text-gray-800">Sign out</span>
                      </button>
                    </div>
                  )}
                </div>
              </SignedIn>
            </div>
          </div>

        {/* Breadcrumb */}
        {selectedSchool && (
          <div className="bg-gray-50 border-t border-gray-100">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 py-2">
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <button onClick={resetAll} className="hover:text-[#e53935]">Home</button>
                <ChevronRight className="w-4 h-4" />
                <span className="text-gray-700">{selectedSchool.name}</span>
                {selectedSport && (
                  <>
                    <ChevronRight className="w-4 h-4" />
                    <span className="text-gray-700">{selectedSport}</span>
                  </>
                )}
                {selectedGame && (
                  <>
                    <ChevronRight className="w-4 h-4" />
                    <span className="text-gray-700">vs {selectedGame.opponent}</span>
                  </>
                )}
              </div>
            </div>
          </div>
        )}
      </header>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        onChange={handleFileSelection}
        className="hidden"
      />

      {/* Step 0: Home / Search - Simplified layout */}
      {step === 0 && (
        <main className="bg-white">
          <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8">
            {/* Hero Banner - full-width image with overlaid text. Put your own image at public/hero-photo.jpg to use it. */}
            <div className="relative rounded-xl overflow-hidden mb-8 min-h-[280px] md:min-h-[360px] bg-gray-900">
              <img
                src="/hero-photo.jpg"
                alt="Sports action shot"
                className="absolute inset-0 w-full h-full object-cover object-[center_25%]"
                onError={(e) => {
                  const img = e.target;
                  if (img.dataset.fallbackUsed) {
                    img.style.display = 'none';
                    const parent = img.parentElement;
                    if (parent && !parent.querySelector('.hero-fallback-bg')) {
                      const fallback = document.createElement('div');
                      fallback.className = 'hero-fallback-bg absolute inset-0 bg-gradient-to-br from-[#e53935] to-[#c62828] flex items-center justify-center';
                      fallback.innerHTML = '<svg class="w-20 h-20 text-white/80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>';
                      parent.appendChild(fallback);
                    }
                    return;
                  }
                  img.dataset.fallbackUsed = '1';
                  img.src = 'https://images.unsplash.com/photo-1574629810360-7efbbe195018?w=1200&q=80';
                }}
              />
              <div className="absolute inset-0 bg-gradient-to-r from-black/75 via-black/50 to-transparent md:from-black/70 md:via-black/40" />
              <div className="relative flex flex-col justify-center min-h-[280px] md:min-h-[360px] px-6 py-12 md:px-12 md:py-16 max-w-2xl">
                <h1 className="text-3xl md:text-5xl font-bold text-white mb-3 md:mb-4 leading-tight drop-shadow-sm">
                  Find Your <span className="text-[#e53935] font-['Bebas_Neue'] tracking-wide">Game Day</span> Photos
                </h1>
                <p className="text-lg md:text-xl text-white/90 max-w-lg drop-shadow-sm">
                  Search your school to browse and purchase high-quality action shots.
                </p>
              </div>
            </div>

            {/* Search Section */}
            <div className="bg-white rounded-xl shadow-sm p-6 border border-gray-100 mb-8">
              <h3 className="text-lg font-semibold text-gray-800 mb-4">Search for your school</h3>
              <div className="relative">
                <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
                <input
                  type="text"
                  placeholder="Enter school name..."
                  className="w-full pl-12 pr-12 py-4 bg-gray-50 border border-gray-200 rounded-xl text-gray-700 placeholder-gray-400 focus:border-[#e53935] focus:outline-none focus:ring-2 focus:ring-[#e53935]/20 transition-all text-base"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  autoFocus
                />
                {searchQuery && (
                  <button onClick={() => setSearchQuery('')} className="absolute right-4 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors">
                    <X className="w-5 h-5" />
                  </button>
                )}
              </div>

              {searchQuery.trim() && (
                <div className="mt-4 border border-gray-100 rounded-lg overflow-hidden">
                  {searchResults.length > 0 ? (
                    searchResults.map((school) => (
                      <button
                        key={school.name}
                        onClick={() => handleSelectSchool(school)}
                        className="w-full text-left px-4 py-4 hover:bg-[#e53935]/5 flex items-center justify-between transition-colors border-b border-gray-100 last:border-b-0"
                      >
                        <div>
                          <div className="font-medium text-gray-800">{school.name}</div>
                          <div className="text-sm text-gray-500 mt-0.5">{school.sports.join(' • ')}</div>
                        </div>
                        <ChevronRight className="w-5 h-5 text-[#e53935]" />
                      </button>
                    ))
                  ) : (
                    <div className="px-4 py-6 text-center text-gray-500 text-sm">
                      No schools found. Try "Lawrence", "Wayne", or "Homewood".
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Professional quality, easy to find, instant download - at the bottom */}
            <div className="border-t border-gray-100 pt-12 pb-8">
              <p className="text-center text-lg text-gray-600 max-w-2xl mx-auto">
                Professional quality, easy to find, and instant download — your game day memories, delivered.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mt-8">
                <div className="text-center p-6">
                  <div className="w-14 h-14 bg-[#e53935]/10 rounded-full flex items-center justify-center mx-auto mb-4">
                    <Camera className="w-7 h-7 text-[#e53935]" />
                  </div>
                  <h3 className="font-semibold text-gray-800 mb-2">Professional Quality</h3>
                  <p className="text-sm text-gray-500">High-resolution photos captured by professional sports photographers</p>
                </div>
                <div className="text-center p-6">
                  <div className="w-14 h-14 bg-[#e53935]/10 rounded-full flex items-center justify-center mx-auto mb-4">
                    <Users className="w-7 h-7 text-[#e53935]" />
                  </div>
                  <h3 className="font-semibold text-gray-800 mb-2">Easy to Find</h3>
                  <p className="text-sm text-gray-500">AI-powered athlete recognition helps you find your photos instantly</p>
                </div>
                <div className="text-center p-6">
                  <div className="w-14 h-14 bg-[#e53935]/10 rounded-full flex items-center justify-center mx-auto mb-4">
                    <ShoppingCart className="w-7 h-7 text-[#e53935]" />
                  </div>
                  <h3 className="font-semibold text-gray-800 mb-2">Instant Download</h3>
                  <p className="text-sm text-gray-500">Purchase and download your photos immediately after checkout</p>
                </div>
              </div>
            </div>
          </div>
        </main>
      )}

      {/* Step 1: Sports Selection */}
      {step === 1 && (
        <main className="max-w-4xl mx-auto px-4 sm:px-6 py-8">
          <button onClick={goBack} className="flex items-center text-sm text-gray-500 hover:text-[#e53935] mb-8 transition-colors">
            <ArrowLeft className="w-4 h-4 mr-2" /> Back to Search
          </button>

          <div className="mb-8">
            <h2 className="text-2xl font-bold text-gray-800 mb-2">Select a Sport</h2>
            <p className="text-gray-500">Choose a sport to view available games and photos</p>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {sports.map((sport) => (
              <button
                key={sport}
                onClick={() => handleSelectSport(sport)}
                className="group p-6 bg-white border-2 border-gray-100 rounded-xl hover:border-[#e53935] hover:shadow-lg transition-all text-left"
              >
                <div className="w-12 h-12 bg-gray-100 group-hover:bg-[#e53935]/10 rounded-lg flex items-center justify-center mb-4 transition-colors">
                  <Trophy className="w-6 h-6 text-gray-400 group-hover:text-[#e53935] transition-colors" />
                </div>
                <div className="font-semibold text-gray-800 group-hover:text-[#e53935] transition-colors">{sport}</div>
              </button>
            ))}
          </div>
        </main>
      )}

      {/* Step 2: Game Schedule */}
      {step === 2 && (
        <main className="max-w-4xl mx-auto px-4 sm:px-6 py-8">
          <button onClick={goBack} className="flex items-center text-sm text-gray-500 hover:text-[#e53935] mb-8 transition-colors">
            <ArrowLeft className="w-4 h-4 mr-2" /> Back to Sports
          </button>

          <div className="mb-8">
            <h2 className="text-2xl font-bold text-gray-800 mb-2">Game Schedule</h2>
            <p className="text-gray-500">{selectedSchool?.name} • {selectedSport}</p>
          </div>

          <div className="space-y-3">
            {schedule.map((game) => (
              <button
                key={game.game_id}
                onClick={() => handleSelectGame(game)}
                className="w-full p-5 bg-white border-2 border-gray-100 rounded-xl hover:border-[#e53935] hover:shadow-lg transition-all flex items-center justify-between text-left group"
              >
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 bg-gray-100 group-hover:bg-[#e53935]/10 rounded-lg flex items-center justify-center transition-colors">
                    <Calendar className="w-5 h-5 text-gray-400 group-hover:text-[#e53935] transition-colors" />
                  </div>
                  <div>
                    <div className="font-semibold text-gray-800">vs {game.opponent}</div>
                    <div className="text-sm text-gray-500 mt-0.5">{game.date}</div>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm text-[#e53935] font-medium">View Photos</span>
                  <ChevronRight className="w-5 h-5 text-[#e53935]" />
                </div>
              </button>
            ))}
            {schedule.length === 0 && (
              <div className="text-center py-16 text-gray-400">No games found for this sport</div>
            )}
          </div>
        </main>
      )}

      {/* Step 3: Athlete Groups / Categories */}
      {step === 3 && (
        <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8">
          <button onClick={goBack} className="flex items-center text-sm text-gray-500 hover:text-[#e53935] mb-6 transition-colors">
            <ArrowLeft className="w-4 h-4 mr-2" /> Back to Schedule
          </button>

          <div className="mb-8">
            <h2 className="text-3xl font-bold text-gray-800 mb-2">Browse Athletes</h2>
            <p className="text-gray-500">vs {selectedGame?.opponent} • {selectedGame?.date}</p>
          </div>

          {uploadMessage && (
            <div className="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-700">
              {uploadMessage}
            </div>
          )}

          {/* Sort/Filter Bar with Upload Button */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-6 pb-6 border-b border-gray-100">
            <div className="flex items-center gap-4">
              <p className="text-sm text-gray-500">{clusters.length} athlete groups found</p>
              
              {/* Sort/Filter Dropdown */}
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-500">Sort by:</span>
                <select className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 focus:border-[#e53935] focus:outline-none">
                  <option>Most Photos</option>
                  <option>Name</option>
                </select>
              </div>
            </div>
            
            {/* Upload Button - Next to the photos */}
            <button
              onClick={() => {
                if (!isSignedIn) {
                  openSignIn();
                } else {
                  fileInputRef.current?.click();
                }
              }}
              disabled={uploading}
              className="flex items-center justify-center gap-2 px-6 py-3 bg-[#e53935] hover:bg-[#c62828] text-white text-base font-bold rounded-xl transition-all shadow-lg hover:shadow-xl disabled:opacity-50 disabled:cursor-not-allowed transform hover:scale-105"
            >
              <Upload className="w-5 h-5" />
              {uploading ? 'Uploading...' : 'Upload Game Photos'}
            </button>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
            {clusters.map((cluster) => {
              const isUnknown = cluster.cluster_id === 'unknown';
              const title = isUnknown ? 'Unknown' : cluster.cluster_id;
              return (
                <button
                  key={cluster.cluster_id}
                  onClick={() => handleOpenCluster(cluster.cluster_id)}
                  className="group text-left bg-white border border-gray-100 rounded-xl overflow-hidden hover:shadow-xl hover:border-[#e53935]/30 transition-all"
                >
                  <div className="aspect-square bg-gray-100 relative overflow-hidden">
                    {cluster.photos[0] ? (
                      <img
                        src={`${API_HOST}${cluster.photos[0].thumbnail_path || cluster.photos[0].image_path}`}
                        alt={cluster.cluster_id}
                        className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-500"
                        loading="lazy"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center">
                        <Users className="w-12 h-12 text-gray-300" />
                      </div>
                    )}
                    <div className="absolute inset-0 bg-gradient-to-t from-black/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                  </div>
                  <div className="p-4">
                    <p className="font-semibold text-gray-800 truncate">{title}</p>
                    <p className="text-sm text-gray-500">{cluster.photo_count} photos</p>
                  </div>
                </button>
              );
            })}
          </div>

          {clusters.length === 0 && (
            <div className="text-center py-20">
              <Camera className="w-16 h-16 text-gray-300 mx-auto mb-4" />
              <p className="text-gray-500 mb-2">No photos available for this game yet</p>
              <p className="text-sm text-gray-400">Be the first to upload photos!</p>
            </div>
          )}
        </main>
      )}

      {/* Step 4: Photo Gallery */}
      {step === 4 && selectedCluster && (
        <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8">
          <section className="relative mb-8 overflow-hidden rounded-3xl border border-slate-200 bg-gradient-to-br from-slate-900 via-slate-800 to-slate-700 px-5 py-6 sm:px-8 sm:py-7">
            <div className="absolute -right-16 -top-20 h-44 w-44 rounded-full bg-[#e53935]/25 blur-2xl" />
            <div className="absolute -bottom-20 left-10 h-52 w-52 rounded-full bg-orange-300/10 blur-3xl" />
            <div className="relative z-10">
              <button onClick={goBack} className="inline-flex items-center rounded-full border border-white/20 bg-white/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.08em] text-slate-100 transition hover:bg-white/20">
                <ArrowLeft className="mr-2 h-4 w-4" /> Back to Athletes
              </button>
              <h2 className="mt-4 text-3xl font-extrabold leading-tight text-white sm:text-4xl">
                {selectedCluster.cluster_id === 'unknown' ? 'Unknown Athlete' : selectedCluster.cluster_id}
              </h2>
              <p className="mt-1 text-sm text-slate-200 sm:text-base">
                {selectedCluster.photo_count} photos ready to purchase
              </p>
              <div className="mt-5 flex flex-wrap items-center gap-3">
                <div className="rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold text-white">
                  {cart.length} in cart
                </div>
                <div className="rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-semibold text-white">
                  {selectedPhotos.length} selected
                </div>
              </div>
            </div>
          </section>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 sm:gap-6">
            {selectedCluster.photos.map((photo) => {
              const isSelected = selectedPhotos.some(p => p.image_url === photo.image_url);
              const inCart = cart.some(p => p.image_url === photo.image_url);
              const packageEligible = photo.include_in_package !== false;
              const packageLabel = getPrimaryPackageDealLabel(photo.uploader_id);
              const isOwnPhoto = photo.uploader_id && user?.id && photo.uploader_id === user.id;
              const photographerName = isOwnPhoto
                ? (user.username || user.primaryEmailAddress?.emailAddress || 'You')
                : (photo.photographer || 'SportsPic Photographer');
              const photographerImage = isOwnPhoto ? user?.imageUrl : photo.photographer_image_url;
              const photographerInitial = (photographerName || 'S').charAt(0).toUpperCase();
              return (
                <div
                  key={photo.image_url}
                  className="group relative overflow-hidden rounded-2xl border border-slate-200/90 bg-white/95 shadow-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-xl"
                >
                  {/* Selection checkbox */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      togglePhotoSelection(photo);
                    }}
                    className={`absolute left-3 top-3 z-10 flex h-7 w-7 items-center justify-center rounded-full border-2 shadow-sm backdrop-blur transition-all ${
                      isSelected 
                        ? 'border-[#e53935] bg-[#e53935] text-white' 
                        : 'border-white/60 bg-black/25 text-transparent hover:border-[#e53935]'
                    }`}
                  >
                    {isSelected && (
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </button>

                  {inCart && (
                    <div className="absolute right-3 top-3 z-10 rounded-full bg-[#e53935] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-white">
                      In Cart
                    </div>
                  )}
                  {packageEligible && (
                    <div className={`absolute right-3 z-10 rounded-full bg-emerald-500 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-white shadow ${inCart ? 'top-11' : 'top-3'}`}>
                      {packageLabel}
                    </div>
                  )}

                  <div 
                    className={`group/image relative aspect-square cursor-pointer overflow-hidden bg-slate-100 ${
                      isSelected ? 'ring-2 ring-[#e53935]' : ''
                    }`}
                    onClick={() => setLightboxPhoto(photo)}
                  >
                    <img
                      src={`${API_HOST}${photo.thumbnail_path || photo.image_path}`}
                      alt={photo.image_url}
                      className="h-full w-full object-cover transition-transform duration-700 group-hover/image:scale-110"
                      loading="lazy"
                    />
                    <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/65 via-transparent to-transparent opacity-85" />
                    <div className="pointer-events-none absolute bottom-2 left-2 rounded-full bg-black/55 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-white">
                      Tap to preview
                    </div>
                  </div>

                  <div className="space-y-3 p-3 sm:p-4">
                    <div className="space-y-1">
                      <p className="truncate text-xs font-semibold uppercase tracking-wide text-slate-400">
                        {photo.image_url.replace(/\.[^/.]+$/, '')}
                      </p>
                      <span className="text-xl font-black text-slate-900">${(photo.price != null ? Number(photo.price) : 5).toFixed(2)}</span>
                    </div>
                    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2">
                      <div className="relative h-7 w-7 overflow-hidden rounded-full bg-[#e53935] text-[11px] font-bold text-white flex items-center justify-center flex-shrink-0">
                        {photographerImage && (
                          <img
                            src={photographerImage}
                            alt=""
                            className="absolute inset-0 h-full w-full object-cover z-[1]"
                            referrerPolicy="no-referrer"
                            onError={(e) => { e.target.style.display = 'none'; }}
                          />
                        )}
                        <span className="relative z-0">{photographerInitial}</span>
                      </div>
                      <p className="truncate text-xs font-medium text-slate-700">{photographerName}</p>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (!inCart) {
                          addToCart([photo]);
                        } else {
                          setShowCart(true);
                        }
                      }}
                      className={`w-full rounded-xl py-2.5 text-sm font-semibold transition-all ${
                        inCart
                          ? 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                          : 'bg-[#e53935] text-white hover:bg-[#c62828]'
                      }`}
                    >
                      {inCart ? 'View Cart' : 'Add to Cart'}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Selection bar */}
          {selectedPhotos.length > 0 && (
            <div className="fixed bottom-5 left-1/2 z-40 flex w-[calc(100%-1.25rem)] max-w-3xl -translate-x-1/2 items-center gap-3 rounded-2xl border border-slate-800 bg-slate-900/95 px-4 py-3 text-white shadow-2xl backdrop-blur sm:bottom-6 sm:gap-6 sm:px-6 sm:py-4">
              <div className="text-sm leading-tight">
                <span className="font-black">{selectedPhotos.length}</span>
                <span className="ml-1 text-slate-300">selected</span>
              </div>
              <div className="h-6 w-px bg-slate-700" />
              <div className="text-lg font-black">
                ${getSelectionTotal().toFixed(2)}
              </div>
              <button
                onClick={clearSelection}
                className="text-sm text-slate-300 transition-colors hover:text-white"
              >
                Clear
              </button>
              <button
                onClick={handleAddSelectedToCart}
                className="ml-auto flex items-center gap-2 rounded-xl bg-[#e53935] px-4 py-2.5 text-sm font-bold text-white transition-colors hover:bg-[#c62828] sm:px-6"
              >
                <ShoppingCart className="w-4 h-4" />
                Add Selected
              </button>
            </div>
          )}
        </main>
      )}

      {/* Cart Slide-out */}
      {showCart && (
        <div 
          className="fixed inset-0 bg-black/40 flex items-stretch justify-end z-50"
          onClick={() => setShowCart(false)}
        >
          <div 
            className="bg-white w-full max-w-md shadow-2xl flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-5 border-b border-gray-100">
              <h2 className="text-xl font-bold text-gray-800">Your Cart ({cart.length})</h2>
              <button
                onClick={() => setShowCart(false)}
                className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-6">
              {cart.length === 0 ? (
                <div className="text-center py-16">
                  <div className="w-20 h-20 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
                    <ShoppingCart className="w-10 h-10 text-gray-400" />
                  </div>
                  <p className="font-semibold text-gray-800 mb-1">Your cart is empty</p>
                  <p className="text-sm text-gray-500">Add some photos to get started</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {cartQuoteLoading && (
                    <div className="px-4 py-3 rounded-xl border border-gray-200 bg-white text-sm text-gray-500">
                      Checking package deals...
                    </div>
                  )}
                  {!cartQuoteLoading && cartQuote?.available_packages?.length > 0 && (
                    <div className="px-4 py-3 rounded-xl border border-gray-200 bg-white">
                      <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">Available Package Deals</p>
                      <div className="space-y-2">
                        {cartQuote.available_packages.map((pkg) => (
                          <div key={`available-${pkg.uploader_id}`} className="text-sm text-gray-700">
                            <p className="font-medium text-gray-800">{pkg.photographer}</p>
                            <p className="text-xs text-gray-500 mb-1">{pkg.eligible_photo_count} eligible photo{pkg.eligible_photo_count !== 1 ? 's' : ''} in your cart</p>
                            <p className="text-xs text-gray-600">
                              {pkg.deals.map((d) => `${d.quantity} for $${(d.package_price_cents / 100).toFixed(2)}`).join(' • ')}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {!cartQuoteLoading && cartQuote?.applied_packages?.length > 0 && (
                    <div className="px-4 py-3 rounded-xl border border-green-200 bg-green-50">
                      <p className="text-xs font-semibold uppercase tracking-wide text-green-700 mb-2">Applied At Checkout</p>
                      <div className="space-y-1.5">
                        {cartQuote.applied_packages.map((pkg, idx) => (
                          <p key={`applied-${idx}`} className="text-xs text-green-800">
                            {pkg.photographer}: {pkg.quantity} photos for ${(pkg.package_price_cents / 100).toFixed(2)} x {pkg.times_applied}
                          </p>
                        ))}
                      </div>
                    </div>
                  )}
                  {cart.map((photo) => (
                    <div key={photo.image_url} className="flex gap-4 p-4 bg-gray-50 rounded-xl">
                      <img
                        src={`${API_HOST}${photo.thumbnail_path || photo.image_path}`}
                        alt={photo.image_url}
                        className="w-24 h-24 object-cover rounded-lg"
                      />
                      <div className="flex-1 min-w-0 flex items-center gap-3">
                        <div className="flex-shrink-0">
                          {(() => {
                            const isOwn = photo.uploader_id && user?.id && photo.uploader_id === user.id;
                            const imgSrc = isOwn ? user?.imageUrl : photo.photographer_image_url;
                            const initial = isOwn ? (user.username || user.primaryEmailAddress?.emailAddress || 'You').charAt(0).toUpperCase() : (photo.photographer || 'S').charAt(0).toUpperCase();
                            return (
                              <div className="w-10 h-10 rounded-full overflow-hidden bg-[#e53935] flex items-center justify-center text-white text-sm font-bold relative">
                                {imgSrc && (
                                  <img src={imgSrc} alt="" className="absolute inset-0 w-full h-full object-cover z-[1]" referrerPolicy="no-referrer" onError={(e) => { e.target.style.display = 'none'; }} />
                                )}
                                <span className="relative z-0">{initial}</span>
                              </div>
                            );
                          })()}
                        </div>
                        <div className="min-w-0">
                          <p className="font-medium text-gray-800 truncate">{photo.image_url.replace(/\.[^/.]+$/, '')}</p>
                          {photo.include_in_package !== false && (
                            <span className="inline-block mt-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700">
                              {getPrimaryPackageDealLabel(photo.uploader_id)}
                            </span>
                          )}
                          <p className="text-sm text-gray-500 mt-0.5">{(photo.uploader_id && user?.id && photo.uploader_id === user.id) ? (user.username || user.primaryEmailAddress?.emailAddress || 'You') : (photo.photographer || 'SportsPic Photographer')}</p>
                          <p className="text-lg font-bold text-[#e53935] mt-2">${(photo.price != null ? Number(photo.price) : 5).toFixed(2)}</p>
                        </div>
                      </div>
                      <button
                        onClick={() => removeFromCart(photo)}
                        className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors self-start"
                      >
                        <X className="w-5 h-5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {cart.length > 0 && (
              <div className="border-t border-gray-100 p-6 space-y-4 bg-gray-50">
                <div className="flex items-center justify-between text-lg">
                  <span className="text-gray-600">Subtotal</span>
                  <span className="font-bold text-gray-800">
                    ${((cartQuote?.subtotal_cents != null ? cartQuote.subtotal_cents / 100 : getCartTotal())).toFixed(2)}
                  </span>
                </div>
                {cartQuote?.savings_cents > 0 && (
                  <div className="flex items-center justify-between text-base">
                    <span className="text-green-700">Package savings</span>
                    <span className="font-bold text-green-700">-${(cartQuote.savings_cents / 100).toFixed(2)}</span>
                  </div>
                )}
                <div className="flex items-center justify-between text-lg pt-1 border-t border-gray-200">
                  <span className="text-gray-700 font-semibold">Total</span>
                  <span className="font-bold text-[#e53935]">
                    ${((cartQuote?.total_cents != null ? cartQuote.total_cents / 100 : getCartTotal())).toFixed(2)}
                  </span>
                </div>
                <button
                  onClick={handleCheckout}
                  className="w-full py-4 text-base font-bold text-white bg-[#e53935] hover:bg-[#c62828] rounded-xl transition-colors shadow-lg"
                >
                  Checkout {cartQuote?.savings_cents > 0 ? `• Save $${(cartQuote.savings_cents / 100).toFixed(2)}` : ''}
                </button>
                <button
                  onClick={clearCart}
                  className="w-full py-2 text-sm text-gray-500 hover:text-gray-700 transition-colors"
                >
                  Clear Cart
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Footer */}
      <footer className="bg-gray-900 text-white mt-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-12">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8 mb-8">
            <div>
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 bg-[#e53935] rounded-lg flex items-center justify-center">
                  <Camera className="w-5 h-5 text-white" />
                </div>
                <span className="text-lg font-bold">SportsPic</span>
              </div>
              <p className="text-sm text-gray-400">Professional sports photography for athletes and teams.</p>
            </div>
            <div>
              <h4 className="font-semibold mb-4">Quick Links</h4>
              <ul className="space-y-2 text-sm text-gray-400">
                <li><a href="#" className="hover:text-white transition-colors">Home</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Browse</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Upload</a></li>
              </ul>
            </div>
            <div>
              <h4 className="font-semibold mb-4">Support</h4>
              <ul className="space-y-2 text-sm text-gray-400">
                <li><a href="#" className="hover:text-white transition-colors">Help Center</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Contact Us</a></li>
                <li><a href="#" className="hover:text-white transition-colors">FAQ</a></li>
              </ul>
            </div>
            <div>
              <h4 className="font-semibold mb-4">Legal</h4>
              <ul className="space-y-2 text-sm text-gray-400">
                <li><a href="#" className="hover:text-white transition-colors">Privacy Policy</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Terms of Service</a></li>
                <li><a href="#" className="hover:text-white transition-colors">Refund Policy</a></li>
              </ul>
            </div>
          </div>
          <div className="border-t border-gray-800 pt-8 text-center text-sm text-gray-500">
            © 2026 SportsPic. All rights reserved.
          </div>
        </div>
      </footer>

      {/* Upload Options Modal */}
      {showUploadModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl w-full max-w-md p-6 shadow-2xl">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-xl font-bold text-gray-800">Upload Options</h3>
              <button
                onClick={handleUploadCancel}
                className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="mb-6 p-4 bg-[#e53935]/10 rounded-xl">
              <p className="text-sm font-medium text-[#e53935]">
                {pendingFiles.length} photo{pendingFiles.length !== 1 ? 's' : ''} ready to upload
              </p>
            </div>

            <div className="space-y-5">
              <div>
                <label htmlFor="photo-price" className="block text-sm font-medium text-gray-700 mb-2">
                  Price per Photo
                </label>
                <div className="relative">
                  <span className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-500 font-medium">$</span>
                  <input
                    id="photo-price"
                    type="number"
                    min="0"
                    step="0.01"
                    placeholder="5.00"
                    value={photoPrice}
                    onChange={(e) => setPhotoPrice(e.target.value)}
                    className="w-full pl-8 pr-4 py-3 bg-gray-50 border border-gray-200 rounded-xl text-gray-700 placeholder-gray-400 focus:border-[#e53935] focus:outline-none focus:ring-2 focus:ring-[#e53935]/20 transition-all"
                  />
                </div>
              </div>

              <div className="flex items-center gap-3">
                <input
                  id="include-package"
                  type="checkbox"
                  checked={includeInPackage}
                  onChange={(e) => setIncludeInPackage(e.target.checked)}
                  className="w-5 h-5 rounded border-gray-300 text-[#e53935] focus:ring-[#e53935] cursor-pointer"
                />
                <label htmlFor="include-package" className="text-sm text-gray-700 cursor-pointer">
                  Include in package deals
                </label>
              </div>
            </div>

            <div className="flex gap-3 mt-8">
              <button
                onClick={handleUploadCancel}
                className="flex-1 px-6 py-3 text-sm font-semibold text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-xl transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUploadConfirm}
                className="flex-1 px-6 py-3 text-sm font-semibold text-white bg-[#e53935] hover:bg-[#c62828] rounded-xl transition-colors shadow-md"
              >
                Upload Photos
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Photo Lightbox */}
      {lightboxPhoto && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4"
          onClick={() => setLightboxPhoto(null)}
        >
          <div 
            className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl max-h-[90vh] flex overflow-hidden relative"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setLightboxPhoto(null)}
              className="absolute top-4 right-4 p-2 bg-white/90 hover:bg-white text-gray-500 hover:text-gray-800 rounded-full transition-colors z-10 shadow-lg"
              title="Close (Esc)"
            >
              <X className="w-6 h-6" />
            </button>

            {/* Image */}
            <div className="flex-1 bg-gray-900 flex items-center justify-center p-4 min-h-[400px]">
              <img
                src={`${API_HOST}${lightboxPhoto.image_path}`}
                alt={lightboxPhoto.image_url}
                className="max-w-full max-h-[80vh] object-contain"
              />
            </div>

            {/* Details Sidebar */}
            <div className="w-[380px] bg-white p-6 flex flex-col overflow-y-auto">
              <div className="mb-4">
                <span className="inline-block px-3 py-1 bg-[#e53935]/10 text-[#e53935] text-xs font-bold rounded-full uppercase">
                  Premium Photo
                </span>
                {lightboxPhoto.include_in_package !== false && (
                  <span className="inline-block ml-2 px-3 py-1 bg-emerald-100 text-emerald-700 text-xs font-bold rounded-full uppercase">
                    {getPrimaryPackageDealLabel(lightboxPhoto.uploader_id)}
                  </span>
                )}
              </div>
              {lightboxPhoto.include_in_package !== false && getAllPackageDealsLabel(lightboxPhoto.uploader_id) && (
                <p className="text-xs text-emerald-700 font-medium mb-4">
                  Deals: {getAllPackageDealsLabel(lightboxPhoto.uploader_id)}
                </p>
              )}

              <h3 className="text-xl font-bold text-gray-800 mb-2">Athlete Action Shot</h3>
              <p className="text-sm text-gray-500 mb-6">{lightboxPhoto.image_url}</p>

              {/* Photographer - clickable to view profile when uploader_id exists */}
              {(lightboxPhoto.uploader_id ? (
                <button
                  type="button"
                  onClick={() => {
                    setViewingProfileUserId(lightboxPhoto.uploader_id);
                    const name = (lightboxPhoto.uploader_id === user?.id) ? (user.username || user.primaryEmailAddress?.emailAddress || 'You') : (lightboxPhoto.photographer || 'Photographer');
                    setViewingProfileDisplayName(name);
                    setLightboxPhoto(null);
                  }}
                  className="w-full flex items-center gap-3 p-4 bg-gray-50 hover:bg-gray-100 rounded-xl mb-6 text-left transition-colors cursor-pointer"
                >
                  <div className="w-12 h-12 rounded-full flex-shrink-0 overflow-hidden bg-[#e53935] flex items-center justify-center text-white font-bold relative">
                    {(() => {
                      const imgSrc = (lightboxPhoto.uploader_id === user?.id) ? user?.imageUrl : lightboxPhoto.photographer_image_url;
                      return imgSrc ? <img src={imgSrc} alt="" className="absolute inset-0 w-full h-full object-cover z-[1]" referrerPolicy="no-referrer" onError={(e) => { e.target.style.display = 'none'; }} /> : null;
                    })()}
                    <span className="relative z-0">
                      {((lightboxPhoto.uploader_id === user?.id) ? (user.username || user.primaryEmailAddress?.emailAddress || 'You') : (lightboxPhoto.photographer || 'S')).charAt(0).toUpperCase()}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-gray-500 uppercase tracking-wide">Photographer</p>
                    <p className="font-semibold text-gray-800">{(lightboxPhoto.uploader_id === user?.id) ? (user.username || user.primaryEmailAddress?.emailAddress || 'You') : (lightboxPhoto.photographer || 'SportsPic Photographer')}</p>
                  </div>
                  <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
                </button>
              ) : (
                <div className="flex items-center gap-3 p-4 bg-gray-50 rounded-xl mb-6">
                  <div className="w-12 h-12 rounded-full flex-shrink-0 overflow-hidden bg-[#e53935] flex items-center justify-center text-white font-bold">
                    {(lightboxPhoto.photographer || 'S').charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <p className="text-xs text-gray-500 uppercase tracking-wide">Photographer</p>
                    <p className="font-semibold text-gray-800">{lightboxPhoto.photographer || 'SportsPic Photographer'}</p>
                  </div>
                </div>
              ))}

              {/* Price */}
              <div className="p-4 border-2 border-[#e53935] rounded-xl mb-6">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-semibold text-gray-800">Standard License</span>
                  <span className="text-2xl font-bold text-[#e53935]">${(lightboxPhoto.price != null ? Number(lightboxPhoto.price) : 5).toFixed(2)}</span>
                </div>
                <p className="text-xs text-gray-500">High-resolution digital download for personal use</p>
              </div>

              {/* Add to Cart Button */}
              {cart.some(p => p.image_url === lightboxPhoto.image_url) ? (
                <button
                  onClick={() => {
                    setLightboxPhoto(null);
                    setShowCart(true);
                  }}
                  className="w-full py-4 bg-gray-800 hover:bg-gray-900 text-white font-bold rounded-xl transition-colors flex items-center justify-center gap-2"
                >
                  <ShoppingCart className="w-5 h-5" />
                  View in Cart
                </button>
              ) : (
                <button
                  onClick={() => {
                    addToCart([lightboxPhoto]);
                    setLightboxPhoto(null);
                    setShowCart(true);
                  }}
                  className="w-full py-4 bg-[#e53935] hover:bg-[#c62828] text-white font-bold rounded-xl transition-colors flex items-center justify-center gap-2 shadow-lg"
                >
                  <ShoppingCart className="w-5 h-5" />
                  Add to Cart
                </button>
              )}

              <div className="mt-auto pt-6 border-t border-gray-100">
                <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">Image Details</h4>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Format</span>
                    <span className="font-medium text-gray-800">High-Res JPG</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">License</span>
                    <span className="font-medium text-gray-800">Personal Use</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
