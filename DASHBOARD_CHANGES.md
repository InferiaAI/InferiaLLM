# Dashboard Changes Required

## Summary

The dashboard needs **3 modifications** to work with the refactored API:

✅ **NewPool.tsx** - Dynamic provider discovery  
✅ **DeploymentDetail.tsx** - Capability-based feature detection  
✅ **Error Handling** - Better provider validation messages  

---

## Changes Made

### 1. **NewPool.tsx** (Lines 12-85, 318-330, 381-389)

**Problem:** Provider list was hardcoded (lines 12-42)

**Solution:** 
- Added dynamic provider fetching from `/inventory/providers`
- Providers now loaded from API with capabilities
- Fallback to hardcoded list if API unavailable (backward compatibility)
- Display capabilities badges (Ephemeral, Auction pricing)

**Key Changes:**
```typescript
// NEW: Fetch registered providers dynamically
const { data: providersData } = useQuery({
    queryKey: ["registeredProviders"],
    queryFn: async () => {
        const res = await computeApi.get('/inventory/providers')
        return res.data.providers
    }
})

// Build provider list from API or fallback
const getProviderMeta = () => {
    if (!providersData) {
        // Fallback to hardcoded list
        return [...]
    }
    // Build from API data with capabilities
    return Object.entries(providersData).map(([id, data]) => ({
        ...,
        capabilities: data.capabilities,
    }))
}
```

**New UI Features:**
- Shows "Ephemeral" badge for DePIN providers
- Shows pricing model (Fixed/Auction/Spot)
- Better error messages when provider validation fails

---

### 2. **DeploymentDetail.tsx** (Lines 1-30, 88-120, 214)

**Problem:** Hardcoded provider checks for terminal tab (lines 201-204)

**Solution:**
- Added provider capabilities fetching
- Created `isComputeDeployment()` function using capabilities
- Terminal tab shown based on `is_ephemeral` capability

**Key Changes:**
```typescript
// NEW: Provider capabilities cache
type ProviderCapabilities = {
    is_ephemeral: boolean;
    supports_log_streaming: boolean;
    adapter_type: string;
}

// NEW: Fetch provider capabilities
const { data: providerCapabilities } = useQuery({
    queryKey: ["providerCapabilities", deployment?.provider],
    queryFn: async () => {
        // Fetch from /inventory/providers and cache
    }
})

// NEW: Capability-based compute detection
const isComputeDeployment = () => {
    if (deployment?.engine === "vllm") return true
    if (providerCapabilities?.is_ephemeral) return true
    // Fallback to legacy checks for backward compatibility
    ...
}
```

---

### 3. **Error Handling** 

**Problem:** Generic error messages when pool creation fails

**Solution:**
- Better error display from API validation
- Clear messages when provider doesn't exist

**Change:**
```typescript
} catch (error: any) {
    const errorDetail = error.response?.data?.detail || error.message
    toast.error(errorDetail)  // Shows: "Invalid provider 'X'. Available providers: [...]"
}
```

---

## API Endpoints Used

| Endpoint | Purpose | New/Existing |
|----------|---------|--------------|
| `GET /inventory/providers` | Fetch all providers with capabilities | **NEW** |
| `GET /provider/resources?provider=X` | Fetch resources for provider | Existing (Modified) |
| `POST /deployment/createpool` | Create compute pool | Existing (Enhanced validation) |

---

## Backward Compatibility

✅ **Fully Backward Compatible**

- Fallback to hardcoded provider list if API fails
- Legacy provider detection still works
- All existing deployments continue to function
- No breaking changes to UI flow

---

## Testing Checklist

### Test Provider Discovery
```bash
# Should show providers from API
curl http://localhost:8080/inventory/providers
```

### Test Pool Creation
1. Open "Create New Compute Pool"
2. Should see all configured providers
3. Should see capability badges (Ephemeral, Auction)
4. Try creating with invalid provider - should show error

### Test Deployment Terminal
1. Open deployment on Nosana/Akash
2. Should see "Terminal Logs" tab
3. Open deployment on non-ephemeral provider
4. Should NOT see "Terminal Logs" tab

---

## Files Modified

1. `/apps/dashboard/src/pages/Compute/NewPool.tsx`
2. `/apps/dashboard/src/pages/DeploymentDetail.tsx`

## Migration Steps

1. **No action required** - Changes are backward compatible
2. Dashboard will automatically:
   - Fetch providers from new endpoint
   - Use capabilities when available
   - Fall back to legacy behavior if API unavailable
3. **Optional:** Clear browser cache to ensure new code loads

---

## Benefits

✅ **Dynamic Providers**: New providers automatically appear in UI  
✅ **Capability Awareness**: UI adapts to provider features  
✅ **Better UX**: Clear error messages and capability indicators  
✅ **Future-Proof**: Works with any new provider type  
✅ **No Breaking Changes**: Existing functionality preserved  

---

## Troubleshooting

**Issue:** Providers not showing
- Check: `GET /inventory/providers` returns data
- Solution: Ensure orchestration service is running

**Issue:** Terminal tab not appearing for DePIN
- Check: Provider capabilities show `is_ephemeral: true`
- Solution: Verify provider adapter sets capability correctly

**Issue:** Pool creation fails with "Invalid provider"
- Check: Provider registered in adapter registry
- Solution: Check `/inventory/providers` lists the provider

---

## Future Enhancements (Optional)

1. **Provider Icons**: Add icon mapping for new providers
2. **Capability Badges**: Show more capability indicators (GPU count, Spot support)
3. **Pricing Comparison**: Show resources from all providers side-by-side
4. **Auto-Provider Selection**: Recommend best provider based on capabilities

---

## Status

✅ **Changes Complete and Tested**  
✅ **Backward Compatible**  
✅ **Production Ready**
