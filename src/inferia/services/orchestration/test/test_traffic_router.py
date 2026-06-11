import unittest
from inferia.services.orchestration.services.traffic_router.engine import TrafficRouter

class TestTrafficRouter(unittest.TestCase):
    def setUp(self):
        self.router = TrafficRouter()

    def test_initialization(self):
        """Test that dummy data is initialized."""
        self.assertIn("inference-engine", self.router.routes)
        self.assertIsNotNone(self.router.get_route("inference-engine"))

    def test_register_service(self):
        """Test registering a new service."""
        self.router.register_service("new-service", ["http://new-service:8080"])
        self.assertIn("new-service", self.router.routes)
        self.assertEqual(self.router.get_route("new-service"), "http://new-service:8080")

    def test_canary_routing_logic(self):
        """Test that traffic is routed to canary based on weight."""
        service_name = "test-canary-service"
        stable_url = "http://stable:80"
        canary_url = "http://canary:80"
        
        self.router.register_service(service_name, [stable_url])
        self.router.add_canary_endpoint(service_name, canary_url)
        
        # 1. Test 0% canary (default)
        # Should always be stable
        for _ in range(50):
            self.assertEqual(self.router.get_route(service_name), stable_url)
            
        # 2. Test 100% canary
        self.router.update_weights(service_name, 100)
        for _ in range(50):
            self.assertEqual(self.router.get_route(service_name), canary_url)

        # 3. Test 50% split (statistical)
        self.router.update_weights(service_name, 50)
        canary_count = 0
        total_requests = 1000
        for _ in range(total_requests):
            if self.router.get_route(service_name) == canary_url:
                canary_count += 1
        
        # Allow some statistical variance, e.g., 40% to 60%
        self.assertTrue(400 <= canary_count <= 600, f"Canary count {canary_count} out of expected range")

    def test_missing_service(self):
        """Test behavior for unknown service."""
        self.assertIsNone(self.router.get_route("non-existent"))

    def test_simulation_workflow(self):
        """Simulate a full rollout workflow: 10% -> 90% -> Error -> Rollback (0%)."""
        service_name = "simulation-service"
        stable_url = "http://stable"
        canary_url = "http://canary"
        
        print("\n\n--- Starting Advanced Canary Simulation ---")
        
        self.router.register_service(service_name, [stable_url])
        self.router.add_canary_endpoint(service_name, canary_url)
        
        def run_batch(num_requests, target_weight, description):
            print(f"\n[Step: {description}]")
            print(f"Target Weight: {target_weight}% Canary")
            counts = {stable_url: 0, canary_url: 0}
            
            for _ in range(num_requests):
                route = self.router.get_route(service_name)
                counts[route] += 1
                
            canary_p = (counts[canary_url] / num_requests) * 100
            print(f"  Requests routed: {num_requests}")
            print(f"  Stable: {counts[stable_url]} ({100 - canary_p:.2f}%)")
            print(f"  Canary: {counts[canary_url]} ({canary_p:.2f}%)")
            
            # Verify we are within ~1% of target (statistical check)
            # For 0% and 100% it should be exact
            if target_weight == 0:
                self.assertEqual(counts[canary_url], 0)
            elif target_weight == 100:
                self.assertEqual(counts[stable_url], 0)
            else:
                self.assertTrue(target_weight - 1.5 <= canary_p <= target_weight + 1.5, 
                                f"Deviation too high: expected {target_weight}, got {canary_p}")
            return canary_p

        # 1. Start with 10%
        print(">> Deploying Canary with 10% traffic...")
        self.router.update_weights(service_name, 10)
        run_batch(100000, 10, "Initial Rollout")
        
        # 2. Simulate "Good Performance" -> Increase to 50%
        print(">> Metrics look good! Scaling up...")
        self.router.update_weights(service_name, 50)
        run_batch(100000, 50, "Scale Up to 50%")

        # 3. Simulate "Good Performance" -> Increase to 90%
        print(">> Metrics look good! Scaling up...")
        self.router.update_weights(service_name, 90)
        run_batch(100000, 90, "Scale Up to 90%")

        # 4. Simulate "Errors Detected" -> Rollback
        print("!! ALERT: High error rate detected on Canary! Rolling back immediately !!")
        self.router.update_weights(service_name, 0)
        run_batch(100000, 0, "Emergency Rollback")
        
        print("--- Simulation Complete ---\n")

if __name__ == '__main__':
    unittest.main()
