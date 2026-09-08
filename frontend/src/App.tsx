import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Protected from "./components/Protected";
import Audit from "./pages/Audit";
import Certification from "./pages/Certification";
import CustomerDetail from "./pages/CustomerDetail";
import Customers from "./pages/Customers";
import Login from "./pages/Login";
import Security from "./pages/Security";
import Users from "./pages/Users";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route path="/" element={<Navigate to="/customers" replace />} />
        <Route path="/customers" element={<Customers />} />
        <Route path="/customers/:id" element={<CustomerDetail />} />
        <Route path="/customers/:id/certification" element={<Certification />} />
        <Route path="/users" element={<Users />} />
        <Route path="/security" element={<Security />} />
        <Route path="/audit" element={<Audit />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
